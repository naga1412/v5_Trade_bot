"""Server-side WS keepalive supervisor.

Removes the "leave a browser tab open all day" dependency for populating
``prediction_validations``. The single ``live_worker`` only ever ran
BTC/USDT @ 1h; every other coin only got predictions when a user happened
to be looking at it. That meant the brain-training dataset was
artificially thin and the unlock-real-money milestone slipped further out
the more we waited for organic user traffic.

This supervisor fans out the existing ``run_live_prediction`` coroutine
across the liquidity-floor-qualified spot-backed cohorts of
``live_fleet_universe`` (``established_top20`` + ``liquidity_added_spot``
— see ``app.shadow.live_fleet_universe``, Phase 4 Task 5) on the 1h
timeframe. Each symbol gets its own asyncio.Task running a Binance SPOT
kline WS subscription; closed candles flow through the normal predictor
→ persist_prediction → record_pending_validation pipeline.

Design notes:
- We reuse ``run_live_prediction`` verbatim — no divergent persist path,
  no duplicated WS plumbing. If a future PR fixes a bug there, the
  keepalive fleet picks it up automatically.
- BTC/USDT @ 1h is skipped by default because the existing singleton
  ``live_worker`` already covers it. Double-persist would just chain-
  conflict on the predictions hash chain.
- Per-symbol child tasks are wrapped in a restart-with-backoff loop so
  a transient Binance hiccup on one symbol doesn't kill the others.
- The fleet is re-read every 1h (Phase 4 Task 5f -- corrected from a
  stale 24h default that predated Task 5c's move to a 6h
  universe-refresh cadence); symbols newly passing the liquidity
  floor get a new child task, symbols failing it get their task
  cancelled — UNLESS they have an open live_trades/shadow_open_positions
  row, in which case cancellation is skipped (Phase 4 Task 5b hard
  open-position override; see ``_refresh_children``). There is no
  separate rank/top_n cutoff on top of the liquidity floor — the floor
  itself is the only membership criterion (2026-08-17 liquidity-floor-
  selector addendum superseded the prior top-20-by-volume selection).
- The supervisor itself heartbeats every 5 min so the watchdog sees us
  even on quiet nights when no candles close.

Geoblocking: this uses the SPOT WS endpoint (stream.binance.com:9443),
which is NOT geoblocked from Hetzner Helsinki — see
[[binance_futures_ws_geoblock]] for the Futures-side caveat. Both
cohorts this supervisor reads (``established_top20`` and
``liquidity_added_spot``) are spot-backed by construction — the
futures-only cohort (``futures_poll``) is served by a separate
supervisor (Phase 4 Task 8), not this one.

Cohort threading (Phase 4 Task 9): ``run_live_prediction`` accepts a
``symbol_source`` cohort tag (Task 1) that ends up on every
predictions/telegram_signals/live_trades/shadow_trades row that symbol
produces. Because this supervisor serves BOTH spot cohorts from one
fleet, the desired-symbol set carries cohort as a 3rd tuple element —
``(symbol_pair, timeframe, cohort)`` — set once in
``_load_keepalive_symbols`` (where the cohort is already known, since we
query the two cohorts as separate ``load_live_fleet_universe`` calls)
and threaded through ``_refresh_children`` into each spawned child via
``_default_runner``, a small adapter that closes ``run_live_prediction``'s
keyword-only ``symbol_source`` param over the plain 3-positional-arg
``SymbolRunner`` shape. This was chosen over the alternative (each child
looking its own cohort up from ``load_live_fleet_universe`` at spawn/
restart time) because the cohort is already in hand at zero extra cost
when the desired set is built — a second lookup would be a redundant DB
round-trip on every child restart and race against a concurrent 24h
refresh. The child-identity dict (``children``) stays keyed by
``(symbol_pair, timeframe)`` only — a symbol belongs to exactly one
cohort at a time by construction (the two ``load_live_fleet_universe``
queries are disjoint), so cohort is spawn-time metadata, not part of a
child's identity. A cohort change on an already-running child (e.g. a
symbol's volume rank drifts across the established_top20 boundary) is
NOT picked up until that child is fully cancelled and respawned — see
``_refresh_children``'s docstring for that documented limitation.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ops.heartbeat import record_heartbeat
from app.shadow.live_fleet_universe import (
    LiveFleetEntry,
    has_open_position,
    load_live_fleet_universe,
)
from app.ws.live_prediction import run_live_prediction

log = logging.getLogger(__name__)


# Knob defaults — kept module-level so tests can monkey-patch without
# touching the supervisor's call sites.
# KEEPALIVE_TOP_N is no longer read by this module's own selection path
# (Phase 4 Task 5b switched _load_keepalive_symbols to the liquidity-floor
# selector, which has no rank cutoff) — kept defined because
# app.healer.detectors and tests/healer/test_detectors.py still import it
# as the historical top-N constant for the C3 detector's own (separate,
# asset_universe-based) "expected fleet" calculation. Do not delete
# without checking those call sites first.
KEEPALIVE_TOP_N: int = 20
KEEPALIVE_TIMEFRAME: str = "1h"
KEEPALIVE_REFRESH_SECONDS: int = 60 * 60  # 1h (was 24h -- stale since Task 5c moved the universe refresh to 6h; see Phase 4 Task 5f)
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
#
# Phase 4 Task 9 cohort-threading design decision: the desired-symbol set
# now carries a 3rd element (cohort — see _load_keepalive_symbols below).
# `run_live_prediction`'s `symbol_source` kwarg is keyword-only, so it
# cannot satisfy a plain `Callable[[str, str, str], ...]` positionally —
# `_default_runner` below is the small adapter that closes over that
# keyword-only param, keeping SymbolRunner's shape uniform for both the
# production runner and every test stand-in. See the module docstring's
# "Cohort threading" section for the full rationale (option (a) of the
# two the 2026-08-17 redraft posed).
SymbolRunner = Callable[[str, str, str], Awaitable[None]]


async def _default_runner(symbol_pair: str, timeframe: str, symbol_source: str) -> None:
    """Adapts run_live_prediction's keyword-only `symbol_source` param to
    the 3-positional-arg SymbolRunner shape below, so the desired-set's
    per-symbol cohort tag (established_top20 / liquidity_added_spot)
    reaches the predictions/telegram_signals/live_trades rows that symbol
    produces, instead of every keepalive-fleet symbol silently collapsing
    to run_live_prediction's own "established_top20" default regardless
    of which cohort it actually came from."""
    await run_live_prediction(symbol_pair, timeframe, symbol_source=symbol_source)


async def _run_child_with_restart(
    runner: SymbolRunner, symbol_pair: str, timeframe: str, symbol_source: str,
) -> None:
    """Wrap a per-symbol runner in a restart-with-backoff loop.

    A single coin throwing repeatedly (delisted, Binance 451, etc.) must
    not take down the rest of the fleet. We exponential-backoff and keep
    retrying; CancelledError propagates so shutdown is clean.

    `symbol_source` is the cohort this child was spawned for (fixed for
    the child's lifetime — a cohort change only takes effect on the next
    full respawn, same staleness window the liquidity floor itself
    already has via the 24h refresh).
    """
    backoff = KEEPALIVE_CHILD_BACKOFF_BASE_S
    while True:
        try:
            await runner(symbol_pair, timeframe, symbol_source)
            # Runner returned normally (shouldn't happen for the live WS
            # path, but we tolerate it for testability) — reset backoff
            # before the next attempt.
            backoff = KEEPALIVE_CHILD_BACKOFF_BASE_S
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — resilient supervisor
            log.warning(
                "ws_keepalive child %s/%s (%s) crashed: %s; restart in %.1fs",
                symbol_pair, timeframe, symbol_source, e, backoff,
            )
            await asyncio.sleep(backoff)
            backoff = min(KEEPALIVE_CHILD_BACKOFF_MAX_S, backoff * 2)


async def _load_keepalive_symbols(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    exclude: frozenset[tuple[str, str]],
    timeframe: str,
) -> list[tuple[str, str, str]]:
    """Read the liquidity-floor-qualified spot-backed cohorts
    (``established_top20`` + ``liquidity_added_spot``) from
    ``live_fleet_universe``, normalize, and apply excludes.

    No ``top_n`` slice — the liquidity floor itself is now the only
    membership criterion (Phase 4 liquidity-floor-selector addendum,
    2026-08-17; see ``app.shadow.live_fleet_universe``). The
    ``futures_poll`` cohort is intentionally excluded here — those
    symbols are served by the separate futures REST-poll supervisor
    (Phase 4 Task 8), not this spot-WS fleet.

    Returns a list of ``(symbol_pair, timeframe, cohort)`` tuples — the
    cohort element is Phase 4 Task 9: each symbol's cohort is known here
    (we already queried the two cohorts separately) and is carried
    forward through ``_refresh_children`` into the ``run_live_prediction``
    call each child eventually makes, instead of being discarded and
    letting every keepalive-fleet symbol collapse to the same default
    regardless of which cohort it actually qualified through.

    Empty result on any failure is logged but not raised — the supervisor
    will retry on its next refresh tick.
    """
    try:
        async with session_factory() as session:
            established: list[LiveFleetEntry] = await load_live_fleet_universe(
                session, cohort="established_top20",
            )
            added: list[LiveFleetEntry] = await load_live_fleet_universe(
                session, cohort="liquidity_added_spot",
            )
    except Exception as e:  # noqa: BLE001
        log.warning("ws_keepalive: load_live_fleet_universe failed: %s", e)
        return []

    triples: list[tuple[str, str, str]] = []
    for cohort, entries in (
        ("established_top20", established),
        ("liquidity_added_spot", added),
    ):
        for entry in entries:
            pair = to_pair(entry.symbol)
            if (pair, timeframe) in exclude:
                continue
            triples.append((pair, timeframe, cohort))
    return triples


async def _refresh_children(
    children: dict[tuple[str, str], asyncio.Task[None]],
    desired: list[tuple[str, str, str]],
    *,
    runner: SymbolRunner,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    """Reconcile the running child tasks with the desired set.

    - New symbols → spawn a child task, threading its cohort (the 3rd
      element of its desired-set entry) into the runner so the eventual
      ``run_live_prediction(symbol_source=...)`` call reflects which of
      the two spot cohorts (``established_top20`` / ``liquidity_added_spot``)
      this symbol actually qualified through — Phase 4 Task 9.
    - Removed symbols → cancel and await the old task, UNLESS the symbol
      has an open live_trades/shadow_open_positions row — Phase 4
      liquidity-floor-selector addendum (a) point 4's hard open-position
      override ("never removed... until the position closes"). The
      override is only checked when ``session_factory`` is supplied;
      callers that omit it (most existing tests, and any caller that
      doesn't care about the override) get the prior unconditional-cancel
      behavior unchanged.
    - Symbols still present → leave untouched (no churn). Note: if a
      symbol's cohort changes between refreshes (e.g. volume rank drift
      moves it from established_top20 to liquidity_added_spot) while it
      stays in the desired set throughout, the already-running child is
      NOT restarted to pick up the new tag — same staleness window the
      liquidity floor membership itself already has (24h refresh cadence
      applies to spawns/cancellations, not to relabeling a live child).
    """
    children_cohort_by_key = {(p, tf): c for p, tf, c in desired}
    desired_set = set(children_cohort_by_key)
    # Cancel symbols that dropped below the liquidity floor (or were
    # excluded/delisted), unless an open position retains them.
    for key in list(children):
        if key not in desired_set:
            if session_factory is not None:
                symbol_pair, _tf = key
                async with session_factory() as session:
                    if await has_open_position(session, symbol_pair):
                        log.info(
                            "ws_keepalive: retaining %s/%s -- open position",
                            *key,
                        )
                        continue
            log.info("ws_keepalive: dropping %s/%s", *key)
            children[key].cancel()
            try:
                await children[key]
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            del children[key]
    # Spawn symbols that joined the fleet.
    for symbol_pair, timeframe, cohort in desired:
        key = (symbol_pair, timeframe)
        if key in children:
            continue
        log.info(
            "ws_keepalive: starting %s/%s (%s)", symbol_pair, timeframe, cohort,
        )
        children[key] = asyncio.create_task(
            _run_child_with_restart(runner, symbol_pair, timeframe, cohort),
            name=f"keepalive:{symbol_pair}:{timeframe}",
        )


async def run_keepalive(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    timeframe: str = KEEPALIVE_TIMEFRAME,
    exclude: frozenset[tuple[str, str]] = DEFAULT_EXCLUDE,
    refresh_seconds: int = KEEPALIVE_REFRESH_SECONDS,
    heartbeat_seconds: int = KEEPALIVE_HEARTBEAT_SECONDS,
    runner: SymbolRunner = _default_runner,
) -> None:
    """Main supervisor loop. Owns the per-symbol fleet for the lifetime
    of the process.

    The loop wakes on the shorter of ``heartbeat_seconds`` or
    ``refresh_seconds``. On each tick it heartbeats; on every Nth tick
    (where N = refresh_seconds / heartbeat_seconds) it re-reads the
    liquidity-floor-qualified fleet and reconciles the children.
    """
    log.info(
        "ws_keepalive: starting (tf=%s, refresh=%ds, hb=%ds)",
        timeframe, refresh_seconds, heartbeat_seconds,
    )

    children: dict[tuple[str, str], asyncio.Task[None]] = {}
    try:
        # Initial population. If the fleet is empty (first boot, table
        # never populated), log + run with no children; the next refresh
        # will pick them up once the daily live-fleet-universe refresh runs.
        desired = await _load_keepalive_symbols(
            session_factory, exclude=exclude, timeframe=timeframe,
        )
        await _refresh_children(
            children, desired, runner=runner, session_factory=session_factory,
        )
        await record_heartbeat(
            session_factory, WORKER_NAME,
            status="ok",
            details={"children": len(children), "timeframe": timeframe},
        )

        # Phase 4 Task 5f root cause: last_refresh MUST be a real captured
        # loop.time() reading, never a hardcoded 0.0. loop.time() is backed
        # by time.monotonic() -> the kernel's CLOCK_MONOTONIC, and Docker
        # containers share their host's kernel (no per-container clock
        # namespace) -- so its absolute value reflects host-wide elapsed
        # time (typically since host boot), not this process/container's
        # start time. On a long-lived host, `now = loop.time()` is already
        # far larger than any refresh_seconds, so a hardcoded 0.0 baseline
        # made `now - 0.0 >= refresh_seconds` trivially true on the very
        # first in-loop check -- reconciliation appeared to work by
        # ACCIDENT, not by design. A host reboot near a container restart
        # resets this clock and would make the identical code genuinely
        # wait the full refresh_seconds before ever reconciling again --
        # an environment-dependent divergence between a long-running host
        # and a freshly-rebooted one. Capturing loop.time() here (after the
        # initial population above, not before it) measures real elapsed
        # time since the last reconciliation, independent of host uptime.
        # DO NOT "simplify" this back to 0.0 -- see
        # docs/superpowers/decisions/2026-08-19-live-fleet-universe-never-
        # scheduled-incident.md and the regression test
        # test_run_keepalive_reconciliation_requires_genuine_elapsed_loop_time.
        loop = asyncio.get_event_loop()
        last_refresh = loop.time()
        while True:
            await asyncio.sleep(heartbeat_seconds)
            now = loop.time()
            if now - last_refresh >= refresh_seconds:
                desired = await _load_keepalive_symbols(
                    session_factory, exclude=exclude, timeframe=timeframe,
                )
                if desired:
                    await _refresh_children(
                        children, desired, runner=runner,
                        session_factory=session_factory,
                    )
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
