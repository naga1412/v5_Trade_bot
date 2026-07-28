"""Server-side WS keepalive supervisor.

Removes the "leave a browser tab open all day" dependency for populating
``prediction_validations``. The single ``live_worker`` only ever ran
BTC/USDT @ 1h; every other coin only got predictions when a user happened
to be looking at it. That meant the brain-training dataset was
artificially thin and the unlock-real-money milestone slipped further out
the more we waited for organic user traffic.

This supervisor fans out the existing ``run_live_prediction`` coroutine
across the top-N universe symbols (default 20) on the 1h timeframe. Each
symbol gets its own asyncio.Task running a Binance SPOT kline WS
subscription; closed candles flow through the normal predictor →
persist_prediction → record_pending_validation pipeline.

Design notes:
- We reuse ``run_live_prediction`` verbatim — no divergent persist path,
  no duplicated WS plumbing. If a future PR fixes a bug there, the
  keepalive fleet picks it up automatically.
- BTC/USDT @ 1h is skipped by default because the existing singleton
  ``live_worker`` already covers it. Double-persist would just chain-
  conflict on the predictions hash chain.
- Per-symbol child tasks are wrapped in a restart-with-backoff loop so
  a transient Binance hiccup on one symbol doesn't kill the others.
- Universe is re-read every 24h; symbols added to the top-N get a new
  child task, symbols dropped get their task cancelled.
- The supervisor itself heartbeats every 5 min so the watchdog sees us
  even on quiet nights when no candles close.

Geoblocking: this uses the SPOT WS endpoint (stream.binance.com:9443),
which is NOT geoblocked from Hetzner Helsinki — see
[[binance_futures_ws_geoblock]] for the Futures-side caveat. The
top-N source ``load_current_universe`` is already SPOT-only since
PR #123.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ops.heartbeat import record_heartbeat
from app.shadow.universe import AssetUniverseEntry, load_current_universe
from app.ws.live_prediction import run_live_prediction

log = logging.getLogger(__name__)


# Knob defaults — kept module-level so tests can monkey-patch without
# touching the supervisor's call sites.
KEEPALIVE_TOP_N: int = 20
KEEPALIVE_TIMEFRAME: str = "1h"
KEEPALIVE_REFRESH_SECONDS: int = 24 * 60 * 60  # 24h
KEEPALIVE_HEARTBEAT_SECONDS: int = 5 * 60  # 5min
KEEPALIVE_CHILD_BACKOFF_BASE_S: float = 5.0
KEEPALIVE_CHILD_BACKOFF_MAX_S: float = 120.0

# Subscriptions already owned by another worker — skip to avoid the
# predictions hash chain conflicting on the same (symbol, timeframe, ts).
DEFAULT_EXCLUDE: frozenset[tuple[str, str]] = frozenset({("BTC/USDT", "1h")})

WORKER_NAME: str = "ws_keepalive_task"


# SPOT symbol → "X/USDT" formatter. The asset_universe table stores
# symbols in Binance no-slash form (BTCUSDT); the rest of the stack uses
# slash form (BTC/USDT). We normalize at this boundary so downstream
# call sites don't have to special-case. Public so the healer C3
# detector can use the same normalization when scoping to the fleet's
# expected prediction set — one source of truth for the pair format.
def to_pair(symbol_no_slash: str) -> str:
    if symbol_no_slash.endswith("USDT"):
        return f"{symbol_no_slash[:-4]}/USDT"
    return symbol_no_slash


# Type alias for the per-symbol runner — parameterized so tests can inject
# a deterministic stand-in instead of opening real WS connections.
SymbolRunner = Callable[[str, str], Awaitable[None]]


async def _run_child_with_restart(
    runner: SymbolRunner, symbol_pair: str, timeframe: str,
) -> None:
    """Wrap a per-symbol runner in a restart-with-backoff loop.

    A single coin throwing repeatedly (delisted, Binance 451, etc.) must
    not take down the rest of the fleet. We exponential-backoff and keep
    retrying; CancelledError propagates so shutdown is clean.
    """
    backoff = KEEPALIVE_CHILD_BACKOFF_BASE_S
    while True:
        try:
            await runner(symbol_pair, timeframe)
            # Runner returned normally (shouldn't happen for the live WS
            # path, but we tolerate it for testability) — reset backoff
            # before the next attempt.
            backoff = KEEPALIVE_CHILD_BACKOFF_BASE_S
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — resilient supervisor
            log.warning(
                "ws_keepalive child %s/%s crashed: %s; restart in %.1fs",
                symbol_pair, timeframe, e, backoff,
            )
            await asyncio.sleep(backoff)
            backoff = min(KEEPALIVE_CHILD_BACKOFF_MAX_S, backoff * 2)


async def _load_keepalive_symbols(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    top_n: int,
    exclude: frozenset[tuple[str, str]],
    timeframe: str,
) -> list[tuple[str, str]]:
    """Read the top-N from asset_universe, normalize, and apply excludes.

    Returns a list of ``(symbol_pair, timeframe)`` tuples. Empty result on
    any failure is logged but not raised — the supervisor will retry on
    its next refresh tick.
    """
    try:
        async with session_factory() as session:
            entries: list[AssetUniverseEntry] = await load_current_universe(session)
    except Exception as e:  # noqa: BLE001
        log.warning("ws_keepalive: load_current_universe failed: %s", e)
        return []

    pairs: list[tuple[str, str]] = []
    for entry in entries[:top_n]:
        pair = to_pair(entry.symbol)
        key = (pair, timeframe)
        if key in exclude:
            continue
        pairs.append(key)
    return pairs


async def _refresh_children(
    children: dict[tuple[str, str], asyncio.Task[None]],
    desired: list[tuple[str, str]],
    *,
    runner: SymbolRunner,
) -> None:
    """Reconcile the running child tasks with the desired set.

    - New symbols → spawn a child task.
    - Removed symbols → cancel and await the old task.
    - Symbols still present → leave untouched (no churn).
    """
    desired_set = set(desired)
    # Cancel symbols that left the top-N (delisted, dropped in rank, …).
    for key in list(children):
        if key not in desired_set:
            log.info("ws_keepalive: dropping %s/%s", *key)
            children[key].cancel()
            try:
                await children[key]
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            del children[key]
    # Spawn symbols that joined the top-N.
    for key in desired:
        if key in children:
            continue
        symbol_pair, timeframe = key
        log.info("ws_keepalive: starting %s/%s", symbol_pair, timeframe)
        children[key] = asyncio.create_task(
            _run_child_with_restart(runner, symbol_pair, timeframe),
            name=f"keepalive:{symbol_pair}:{timeframe}",
        )


async def run_keepalive(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    top_n: int = KEEPALIVE_TOP_N,
    timeframe: str = KEEPALIVE_TIMEFRAME,
    exclude: frozenset[tuple[str, str]] = DEFAULT_EXCLUDE,
    refresh_seconds: int = KEEPALIVE_REFRESH_SECONDS,
    heartbeat_seconds: int = KEEPALIVE_HEARTBEAT_SECONDS,
    runner: SymbolRunner = run_live_prediction,
) -> None:
    """Main supervisor loop. Owns the per-symbol fleet for the lifetime
    of the process.

    The loop wakes on the shorter of ``heartbeat_seconds`` or
    ``refresh_seconds``. On each tick it heartbeats; on every Nth tick
    (where N = refresh_seconds / heartbeat_seconds) it re-reads the
    universe and reconciles the fleet.
    """
    log.info(
        "ws_keepalive: starting (top_n=%d, tf=%s, refresh=%ds, hb=%ds)",
        top_n, timeframe, refresh_seconds, heartbeat_seconds,
    )

    children: dict[tuple[str, str], asyncio.Task[None]] = {}
    try:
        # Initial population. If the universe is empty (first boot, table
        # never populated), log + run with no children; the next refresh
        # will pick them up once the daily universe_refresh_task runs.
        desired = await _load_keepalive_symbols(
            session_factory, top_n=top_n, exclude=exclude, timeframe=timeframe,
        )
        await _refresh_children(children, desired, runner=runner)
        await record_heartbeat(
            session_factory, WORKER_NAME,
            status="ok",
            details={"children": len(children), "timeframe": timeframe},
        )

        last_refresh = 0.0
        loop = asyncio.get_event_loop()
        while True:
            await asyncio.sleep(heartbeat_seconds)
            now = loop.time()
            if now - last_refresh >= refresh_seconds:
                desired = await _load_keepalive_symbols(
                    session_factory, top_n=top_n, exclude=exclude,
                    timeframe=timeframe,
                )
                if desired:
                    await _refresh_children(children, desired, runner=runner)
                    last_refresh = now
                else:
                    log.info(
                        "ws_keepalive: refresh returned 0 symbols; "
                        "keeping existing fleet of %d", len(children),
                    )
            await record_heartbeat(
                session_factory, WORKER_NAME,
                status="ok",
                details={"children": len(children), "timeframe": timeframe},
            )
    finally:
        # Clean shutdown on cancellation: cancel every child and wait.
        for task in children.values():
            task.cancel()
        for task in children.values():
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass


def start_keepalive_task(
    session_factory: async_sessionmaker[AsyncSession],
) -> asyncio.Task[None]:
    """Spawn the supervisor as a single asyncio.Task. Mirrors the other
    ``start_*_task`` factories in app/main.py for symmetry."""
    return asyncio.create_task(run_keepalive(session_factory))


__all__ = [
    "DEFAULT_EXCLUDE",
    "KEEPALIVE_HEARTBEAT_SECONDS",
    "KEEPALIVE_REFRESH_SECONDS",
    "KEEPALIVE_TIMEFRAME",
    "KEEPALIVE_TOP_N",
    "WORKER_NAME",
    "run_keepalive",
    "start_keepalive_task",
    "to_pair",
]
