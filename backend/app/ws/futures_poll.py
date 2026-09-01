"""Phase 4 -- REST-polling supervisor for futures-only symbols.

Mirrors app.ws.keepalive's fleet-of-independent-children pattern, but
polls Binance Futures REST klines every ~60s instead of subscribing to
a WS stream (the geoblocked Futures WS is not usable from this host --
see [[binance_futures_ws_geoblock]]). Feeds the same run_live_prediction
entrypoint the spot-WS fleet uses, via the candle_source injection point
added in Phase 4 Step 0 -- scoring/gating/dispatch/persistence are
byte-identical between the two fleets; only candle delivery differs.

This module is a fully separate supervisor from ws_keepalive_task -- own
child-task set, own reconciliation loop -- so a bug anywhere in this
file cannot reach the spot-WS fleet's tasks (see the design spec's
"Isolation" section for the full argument).
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.data.adapters._base import Candle
from app.data.ratelimit import RateLimitedClient
from app.ops.alert_routing import alert_admin
from app.ops.heartbeat import record_heartbeat
from app.shadow.live_fleet_universe import LiveFleetEntry, has_open_position, load_live_fleet_universe
from app.shadow.multi_stream import MultiStreamCandle
from app.ws.live_prediction import run_live_prediction

log = logging.getLogger(__name__)


async def _load_watermark(
    session_factory: async_sessionmaker[AsyncSession], symbol: str, timeframe: str,
) -> int | None:
    async with session_factory() as session:
        row = (await session.execute(
            sa.text(
                "SELECT last_open_time FROM live_prediction_watermarks "
                "WHERE symbol = :symbol AND timeframe = :timeframe"
            ),
            {"symbol": symbol, "timeframe": timeframe},
        )).one_or_none()
    return int(row.last_open_time) if row is not None else None


async def _save_watermark(
    session_factory: async_sessionmaker[AsyncSession],
    symbol: str, timeframe: str, open_time: int,
) -> None:
    async with session_factory() as session:
        dialect = session.bind.dialect.name if session.bind else "postgresql"
        if dialect.startswith("postgres"):
            sql = (
                "INSERT INTO live_prediction_watermarks (symbol, timeframe, last_open_time, updated_at) "
                "VALUES (:symbol, :timeframe, :open_time, now()) "
                "ON CONFLICT (symbol, timeframe) DO UPDATE "
                "SET last_open_time = EXCLUDED.last_open_time, updated_at = now()"
            )
        else:
            sql = (
                "INSERT INTO live_prediction_watermarks (symbol, timeframe, last_open_time) "
                "VALUES (:symbol, :timeframe, :open_time) "
                "ON CONFLICT (symbol, timeframe) DO UPDATE "
                "SET last_open_time = excluded.last_open_time"
            )
        await session.execute(sa.text(sql), {
            "symbol": symbol, "timeframe": timeframe, "open_time": open_time,
        })
        await session.commit()


# --- Task 7: futures_rest_poll_candles ---------------------------------------

_BASE_URL = "https://fapi.binance.com"

# Real Binance kline `open_time` values are genuine Unix-epoch milliseconds,
# so these MUST stay at true-ms scale to be correct against production data
# (this is what the gap-detection math below is measured against).
_INTERVAL_SECONDS_MS: dict[str, int] = {"1h": 3_600_000, "15m": 900_000}

_RATE_LIMIT_WAIT_LOG_THRESHOLD_S: float = 0.5
_RATE_LIMIT_WAIT_COUNT: dict[str, int] = {}
_GAP_COUNT: dict[str, int] = {}

_CONSECUTIVE_FAILURE_ALERT_THRESHOLD: int = 20
_consecutive_failures: dict[str, int] = {}


async def fetch_futures_seed_klines(
    symbol_pair: str,
    timeframe: str,
    *,
    rate_client: RateLimitedClient,
    limit: int,
) -> list[Candle]:
    """One-shot REST fetch of up to `limit` historical futures klines --
    used only once, at child startup, to seed run_live_prediction's initial
    history DataFrame before futures_rest_poll_candles (Task 7) takes over
    the ongoing feed.

    2026-09-01 root cause: run_live_prediction's default seed path fetches
    from Binance SPOT (app.data.adapters.binance.BinanceClient), which is
    correct for the spot-WS fleet but a GUARANTEED PERMANENT failure for
    every symbol in this cohort -- futures-only symbols have no spot pair
    by definition, that is the entire reason futures_poll exists for them.
    Confirmed empirically: zero predictions, ever, from symbol_source=
    'futures_poll' between this module's Stage 1 promotion (2026-08-31)
    and this fix -- not a handful of edge-case tickers, the entire cohort,
    100% of its existence. See
    docs/superpowers/decisions/2026-09-01-futures-poll-seed-was-spot-only.md.
    """
    binance_symbol = symbol_pair.replace("/", "")
    resp = await rate_client.request(
        "GET", f"{_BASE_URL}/fapi/v1/klines",
        endpoint_key="klines",
        params={"symbol": binance_symbol, "interval": timeframe, "limit": str(limit)},
        timeout=15.0,
    )
    resp.raise_for_status()
    return [
        Candle(
            ts=datetime.fromtimestamp(int(row[0]) / 1000, tz=timezone.utc),
            open=float(row[1]), high=float(row[2]), low=float(row[3]),
            close=float(row[4]), volume=float(row[5]),
        )
        for row in resp.json()
    ]


def _record_poll_result(symbol_pair: str, *, ok: bool) -> None:
    if ok:
        _consecutive_failures[symbol_pair] = 0
        return
    _consecutive_failures[symbol_pair] = _consecutive_failures.get(symbol_pair, 0) + 1
    streak = _consecutive_failures[symbol_pair]
    if streak >= _CONSECUTIVE_FAILURE_ALERT_THRESHOLD:
        log.error(
            "futures_poller: %s has failed %d consecutive polls -- "
            "this symbol's poller looks broken, not a one-off network blip",
            symbol_pair, streak,
        )


def _clear_poll_failure_streaks_for_tests() -> None:
    _consecutive_failures.clear()
    _RATE_LIMIT_WAIT_COUNT.clear()
    _GAP_COUNT.clear()


def _to_multistream_candle(symbol_pair: str, timeframe: str, row: list) -> MultiStreamCandle:
    open_time_ms = int(row[0])
    return MultiStreamCandle(
        symbol=symbol_pair.replace("/", ""), timeframe=timeframe,
        ts=datetime.fromtimestamp(open_time_ms / 1000, tz=timezone.utc),
        open=float(row[1]), high=float(row[2]), low=float(row[3]),
        close=float(row[4]), volume=float(row[5]),
    )


async def _sleep_or_stop(_sleep: Callable[[float], Awaitable[None]], poll_interval_s: float) -> bool:
    """Runs the injected sleep. Returns False if the generator should end.

    Production `_sleep` (`asyncio.sleep`) never raises `StopAsyncIteration`,
    so this is a no-op passthrough there. Tests inject a `_sleep` that raises
    `StopAsyncIteration` after N calls to deterministically end the poll
    loop. That exception must be caught HERE, inside a plain helper, and
    turned into a `return`-driven signal rather than left to propagate
    out of the async generator's own frame: CPython converts any
    `StopAsyncIteration` that is *raised* (as opposed to occurring via a
    generator naturally finishing) while unwinding an async generator's
    frame into a `RuntimeError` ("async generator raised StopAsyncIteration")
    -- this is intentional language-level behavior (mirrors PEP 479 for
    sync generators) that exists specifically so a raised StopAsyncIteration
    can never be confused with genuine iterator exhaustion. Catching it here
    and returning a plain bool lets the caller `return` from the generator
    function itself, which IS the correct way to produce a clean
    StopAsyncIteration for whoever is driving the generator.
    """
    try:
        await _sleep(poll_interval_s)
        return True
    except StopAsyncIteration:
        return False


async def futures_rest_poll_candles(
    symbol_pair: str,
    timeframe: str,
    *,
    rate_client: RateLimitedClient,
    session_factory: async_sessionmaker[AsyncSession],
    poll_interval_s: float = 60.0,
    _sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> AsyncIterator[MultiStreamCandle]:
    """REST-poll Binance Futures klines every ~poll_interval_s, yielding
    only newly-closed candles (open-time advancing past the last one
    processed -- never wall-clock). At-most-once per (symbol, timeframe,
    open_time) via the persisted watermark table; skip-forward on a gap
    (never backfills); WARNING on every fetch failure, ERROR escalation
    on a systematic (consecutive) streak.

    The watermark is loaded once, at generator start, and saved only AFTER
    a yielded candle's downstream processing has completed -- i.e. on the
    resumption following `yield`, not immediately after it. Async-generator
    semantics guarantee that resumption only happens once the caller's
    `async for` loop has finished with the yielded value, so a candle that
    was never actually processed end-to-end (crash, restart, exception in
    the consumer) can never advance the persisted watermark past it.
    """
    binance_symbol = symbol_pair.replace("/", "")
    interval_ms = _INTERVAL_SECONDS_MS[timeframe]
    watermark = await _load_watermark(session_factory, symbol_pair, timeframe)

    while True:
        t0 = time.monotonic()
        try:
            resp = await rate_client.request(
                "GET", f"{_BASE_URL}/fapi/v1/klines",
                endpoint_key="klines",
                params={"symbol": binance_symbol, "interval": timeframe, "limit": "2"},
                timeout=10.0,
            )
            resp.raise_for_status()
            rows = resp.json()
            _record_poll_result(symbol_pair, ok=True)
        except Exception as e:  # noqa: BLE001 -- fail-loud: every fetch failure must be
            # observable, not silently swallowed at DEBUG (this project has a
            # documented history of DEBUG-level swallows hiding months-long
            # outages), so we deliberately catch broadly here and log at
            # WARNING minimum on every single attempt.
            log.warning("futures_poller: fetch failed for %s/%s: %s", symbol_pair, timeframe, e)
            _record_poll_result(symbol_pair, ok=False)
            if not await _sleep_or_stop(_sleep, poll_interval_s):
                return
            continue

        wait_s = time.monotonic() - t0
        if wait_s > _RATE_LIMIT_WAIT_LOG_THRESHOLD_S:
            log.warning(
                "futures_poller: rate-limit wait %.2fs for %s/%s",
                wait_s, symbol_pair, timeframe,
            )
            _RATE_LIMIT_WAIT_COUNT[symbol_pair] = _RATE_LIMIT_WAIT_COUNT.get(symbol_pair, 0) + 1

        if len(rows) >= 2:
            closed_row = rows[-2]
            closed_open_time = int(closed_row[0])

            if watermark is None or closed_open_time > watermark:
                if watermark is not None:
                    expected_next = watermark + interval_ms
                    if closed_open_time > expected_next:
                        gap = (closed_open_time - expected_next) // interval_ms
                        log.error(
                            "futures_poller: gap detected %s/%s, skipped ~%d candle(s)",
                            symbol_pair, timeframe, gap,
                        )
                        _GAP_COUNT[symbol_pair] = _GAP_COUNT.get(symbol_pair, 0) + 1

                candle = _to_multistream_candle(symbol_pair, timeframe, closed_row)
                yield candle
                # Resumed only after the consumer has fully finished
                # processing `candle` -- async-generator semantics
                # guarantee the watermark never advances past a candle
                # that wasn't actually processed end-to-end.
                watermark = closed_open_time
                await _save_watermark(session_factory, symbol_pair, timeframe, watermark)

        if not await _sleep_or_stop(_sleep, poll_interval_s):
            return


# --- Task 8: futures_poll_task supervisor -------------------------------
#
# Mirrors app.ws.keepalive's (post-Task-5b) fleet-of-independent-children
# pattern -- own child-task set, own reconciliation loop, own heartbeat --
# but the desired set comes from load_live_fleet_universe's futures_poll
# cohort (Task 5's liquidity-floor selector) instead of a fixed top-N, and
# each child is fed by futures_rest_poll_candles (Task 7) rather than a
# Binance SPOT WS subscription.
#
# REDRAFTED 2026-08-17 (see the plan doc's own note above this section):
# there is no rank-based selection here at all -- the liquidity floor
# itself is the only membership criterion. FUTURES_POLL_SAFETY_MAX_N is a
# fallback safety valve only (see _load_desired_futures_symbols), never
# the primary selector. And the drop-out path carries the same hard
# open-position override Task 5b shipped for the spot-WS fleet: a symbol
# that no longer qualifies is retained, not cancelled, while it still has
# an open live_trades/shadow_open_positions row.

WORKER_NAME: str = "futures_poll_task"
FUTURES_POLL_SAFETY_MAX_N: int = 30  # safety valve, not the selector -- see plan doc
FUTURES_POLL_TIMEFRAME: str = "1h"
FUTURES_POLL_REFRESH_SECONDS: int = 60 * 60  # 1h (was 24h -- see keepalive.py's identical fix, Phase 4 Task 5f)
FUTURES_POLL_HEARTBEAT_SECONDS: int = 5 * 60  # 5min
_CHILD_BACKOFF_BASE_S: float = 5.0
_CHILD_BACKOFF_MAX_S: float = 120.0

# 2026-09-01: a child that crashes at STARTUP (before it ever produces a
# candle) is structurally different from one that crashed mid-stream after
# running fine for a while -- the former is the "will never succeed"
# pattern (e.g. the spot-seed-on-a-futures-only-symbol bug this same fix
# closes), the latter is far more likely a transient blip. This threshold
# only needs to be small enough to catch a *permanent* failure quickly:
# backoff has already reached its 40-80s range by the 4th-5th consecutive
# crash, which is ample room for a genuine transient issue (a momentary
# Binance 5xx, a brief network blip) to have already cleared on retry.
# Chosen independently from _CONSECUTIVE_FAILURE_ALERT_THRESHOLD (20),
# which paces individual in-stream poll failures at a ~60s cadence -- a
# much faster, noisier signal than this file's own restart-with-backoff
# loop, so the two thresholds are not meant to be the same number.
_CHILD_DEAD_THRESHOLD: int = 5
_child_crash_streaks: dict[tuple[str, str], int] = {}
_confirmed_dead_children: set[tuple[str, str]] = set()


def _clear_child_crash_state_for_tests() -> None:
    _child_crash_streaks.clear()
    _confirmed_dead_children.clear()


# Type alias for the per-symbol runner -- parameterized so tests can inject
# a deterministic stand-in instead of hitting the real REST poller.
FuturesRunner = Callable[[str, str], Awaitable[None]]


async def _default_futures_runner(symbol_pair: str, timeframe: str) -> None:
    """Production runner: run_live_prediction fed by an injected
    futures_rest_poll_candles source -- the entire point of Phase 4 Step 0's
    candle_source injection point is that scoring/gating/dispatch/
    persistence stay byte-identical to the spot-WS fleet; only candle
    delivery differs.

    Local imports (matching app.core.features.flow_features's
    _resolve_rate_client) so importing this module doesn't eagerly build a
    DB session factory / shared httpx client at import time.
    """
    from app.data.adapters import get_intermarket_adapter
    from app.db.session import get_session_factory

    session_factory = get_session_factory()
    rate_client = get_intermarket_adapter().rate_client
    assert rate_client is not None
    source = futures_rest_poll_candles(
        symbol_pair, timeframe, rate_client=rate_client, session_factory=session_factory,
    )
    await run_live_prediction(
        symbol_pair=symbol_pair, timeframe=timeframe,
        candle_source=source, symbol_source="futures_poll",
    )


async def _run_futures_child_with_restart(
    runner: FuturesRunner,
    symbol_pair: str,
    timeframe: str,
    *,
    backoff_base_s: float = _CHILD_BACKOFF_BASE_S,
) -> None:
    """Wrap a per-symbol runner in a restart-with-backoff loop -- mirrors
    keepalive.py's _run_child_with_restart exactly. A single symbol
    throwing repeatedly (delisted, Binance error, etc.) must not take down
    the rest of the futures-poll fleet.

    2026-09-01: also tracks a per-(symbol, timeframe) consecutive-crash
    streak. A streak that reaches _CHILD_DEAD_THRESHOLD is a structurally
    permanent failure, not a transient blip -- logged at ERROR (not
    WARNING, which pages nobody and lets a dead symbol sit silently
    forever) and pushed through the real alert_admin("critical") channel
    once, on the crossing, not on every subsequent retry. The symbol is
    also added to _confirmed_dead_children so run_futures_poll's heartbeat
    can report a coverage count that means what it says (see
    docs/superpowers/decisions/2026-09-01-futures-poll-seed-was-spot-only.md,
    part c) -- the child keeps retrying at its capped backoff regardless
    (self-heals automatically if the underlying condition ever resolves),
    it is only EXCLUDED FROM THE COUNT, never killed outright."""
    key = (symbol_pair, timeframe)
    backoff = backoff_base_s
    while True:
        try:
            await runner(symbol_pair, timeframe)
            backoff = backoff_base_s
            if _child_crash_streaks.pop(key, None):
                _confirmed_dead_children.discard(key)
                log.info(
                    "futures_poll child %s/%s recovered -- clearing dead-child state",
                    symbol_pair, timeframe,
                )
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 -- resilient supervisor
            streak = _child_crash_streaks.get(key, 0) + 1
            _child_crash_streaks[key] = streak
            if streak >= _CHILD_DEAD_THRESHOLD:
                newly_dead = key not in _confirmed_dead_children
                _confirmed_dead_children.add(key)
                log.error(
                    "futures_poll child %s/%s has crashed %d times in a row -- "
                    "treating as structurally dead, excluded from the "
                    "children_producing count until it recovers: %s",
                    symbol_pair, timeframe, streak, e,
                )
                if newly_dead:
                    await alert_admin(
                        f"futures_poll: {symbol_pair}/{timeframe} has crashed "
                        f"{streak} times in a row and is now excluded from "
                        f"coverage -- structurally permanent failure, not a "
                        f"transient blip: {e}",
                        level="critical",
                    )
            else:
                log.warning(
                    "futures_poll child %s/%s crashed: %s; restart in %.1fs",
                    symbol_pair, timeframe, e, backoff,
                )
            await asyncio.sleep(backoff)
            backoff = min(_CHILD_BACKOFF_MAX_S, backoff * 2)


async def _load_desired_futures_symbols(
    session_factory: async_sessionmaker[AsyncSession], *, timeframe: str,
) -> list[tuple[str, str]]:
    """Read the futures_poll cohort from live_fleet_universe -- no top_n
    slice, the liquidity floor itself is the only membership criterion
    (Phase 4 liquidity-floor-selector addendum, 2026-08-17).

    FUTURES_POLL_SAFETY_MAX_N is a fallback safety valve only, per the
    addendum's cost-check section (b): if the floor-qualified futures-only
    cohort ever exceeds it (a market-condition shift qualifying far more
    symbols than today's measured 8-11), truncate to the top N ranked by
    liquidity -- best qvol_24h, tie-broken by tightest spread_bps, then
    highest depth_0_5pct_usdt -- never by volume alone. At today's scale
    this branch never engages; it exists so such a shift can't silently
    blow past whatever a staging soak validated.

    Empty result (any failure, or a genuinely empty cohort) is logged but
    not raised -- the supervisor retries on its next refresh tick, mirroring
    _load_keepalive_symbols's failure handling.
    """
    from app.ws.keepalive import to_pair

    try:
        async with session_factory() as session:
            entries: list[LiveFleetEntry] = await load_live_fleet_universe(
                session, cohort="futures_poll",
            )
    except Exception as e:  # noqa: BLE001
        log.warning("futures_poll: load_live_fleet_universe failed: %s", e)
        return []

    if len(entries) > FUTURES_POLL_SAFETY_MAX_N:
        log.warning(
            "futures_poll: %d qualifying symbols exceeds safety valve %d -- "
            "truncating by liquidity rank, this should be investigated "
            "(staging soak may not cover this scale)",
            len(entries), FUTURES_POLL_SAFETY_MAX_N,
        )
        entries = sorted(
            entries, key=lambda e: (-e.qvol_24h, e.spread_bps, -e.depth_0_5pct_usdt),
        )[:FUTURES_POLL_SAFETY_MAX_N]

    return [(to_pair(e.symbol), timeframe) for e in entries]


async def _refresh_futures_children(
    children: dict[tuple[str, str], asyncio.Task[None]],
    desired: list[tuple[str, str]],
    *,
    runner: FuturesRunner,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    """Reconcile running children with the desired set. Mirrors the
    post-Task-5b keepalive.py's _refresh_children exactly, INCLUDING the
    open-position override -- the liquidity-floor-selector addendum's hard
    open-position override is a requirement on both fleets, not just the
    spot-WS one.

    - New symbols -> spawn a child task.
    - Removed symbols -> cancel and await the old task, UNLESS the symbol
      has an open live_trades/shadow_open_positions row, in which case it
      is retained. The override is only checked when session_factory is
      supplied (default None) -- callers that omit it get the prior
      unconditional-cancel behavior unchanged, which is what makes this a
      base case rather than a breaking change to every existing call site.
    - Symbols still present -> left untouched (no churn, no reconnect).
    """
    desired_set = set(desired)
    for key in list(children):
        if key not in desired_set:
            if session_factory is not None:
                symbol_pair, _tf = key
                async with session_factory() as session:
                    if await has_open_position(session, symbol_pair):
                        log.info("futures_poll: retaining %s/%s -- open position", *key)
                        continue
            log.info("futures_poll: dropping %s/%s", *key)
            children[key].cancel()
            try:
                await children[key]
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            del children[key]
            _child_crash_streaks.pop(key, None)
            _confirmed_dead_children.discard(key)
    for key in desired:
        if key in children:
            continue
        symbol_pair, timeframe = key
        log.info("futures_poll: starting %s/%s", symbol_pair, timeframe)
        children[key] = asyncio.create_task(
            _run_futures_child_with_restart(runner, symbol_pair, timeframe),
            name=f"futures_poll:{symbol_pair}:{timeframe}",
        )


def _heartbeat_details(children: dict[tuple[str, str], asyncio.Task[None]], timeframe: str) -> dict:
    """2026-09-01 (part c of the futures-poll-seed fix): `children` alone
    conflates "we spawned a task for this symbol" with "this symbol is
    actually producing predictions" -- the exact gap that let a 61-symbol
    count sit next to 54 real predictions, or worse, 0-of-7 for a
    structurally dead cohort, without either number ever being reported.
    `children_producing` is the count that should be read as "coverage";
    `dead_symbols` names anything currently excluded from it and why (see
    _run_futures_child_with_restart's _CHILD_DEAD_THRESHOLD escalation).
    `children` (raw task count) is kept for back-compat with existing
    dashboards/probes that already read this key."""
    dead = sorted(f"{s}/{tf}" for s, tf in children if (s, tf) in _confirmed_dead_children)
    return {
        "children": len(children),
        "children_producing": len(children) - len(dead),
        "dead_symbols": dead,
        "timeframe": timeframe,
        "gap_counts": dict(_GAP_COUNT),
        "rate_limit_waits": dict(_RATE_LIMIT_WAIT_COUNT),
    }


async def run_futures_poll(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    timeframe: str = FUTURES_POLL_TIMEFRAME,
    refresh_seconds: int = FUTURES_POLL_REFRESH_SECONDS,
    heartbeat_seconds: int = FUTURES_POLL_HEARTBEAT_SECONDS,
    runner: FuturesRunner = _default_futures_runner,
) -> None:
    """Main supervisor loop -- structurally identical to run_keepalive
    (app.ws.keepalive), owning a completely separate child-task set so a
    bug here can never reach the spot-WS fleet's tasks."""
    log.info(
        "futures_poll: starting (tf=%s, refresh=%ds, hb=%ds)",
        timeframe, refresh_seconds, heartbeat_seconds,
    )
    children: dict[tuple[str, str], asyncio.Task[None]] = {}
    try:
        desired = await _load_desired_futures_symbols(session_factory, timeframe=timeframe)
        await _refresh_futures_children(
            children, desired, runner=runner, session_factory=session_factory,
        )
        await record_heartbeat(
            session_factory, WORKER_NAME, status="ok",
            details=_heartbeat_details(children, timeframe),
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
        # scheduled-incident.md and keepalive.py's identical fix +
        # regression test.
        loop = asyncio.get_event_loop()
        last_refresh = loop.time()
        while True:
            await asyncio.sleep(heartbeat_seconds)
            now = loop.time()
            if now - last_refresh >= refresh_seconds:
                desired = await _load_desired_futures_symbols(session_factory, timeframe=timeframe)
                if desired:
                    await _refresh_futures_children(
                        children, desired, runner=runner, session_factory=session_factory,
                    )
                    last_refresh = now
                else:
                    log.info(
                        "futures_poll: refresh returned 0 symbols; "
                        "keeping existing fleet of %d", len(children),
                    )
            await record_heartbeat(
                session_factory, WORKER_NAME, status="ok",
                details={
                    "children": len(children), "timeframe": timeframe,
                    "gap_counts": dict(_GAP_COUNT), "rate_limit_waits": dict(_RATE_LIMIT_WAIT_COUNT),
                },
            )
    finally:
        for task in children.values():
            task.cancel()
        for task in children.values():
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass


def start_futures_poll_task(
    session_factory: async_sessionmaker[AsyncSession],
) -> asyncio.Task[None]:
    """Spawn the supervisor as a single asyncio.Task. Mirrors
    start_keepalive_task (app.ws.keepalive) for symmetry. Not yet called
    from app/main.py -- that wiring is Phase 4 Task 17's own PR."""
    return asyncio.create_task(run_futures_poll(session_factory))
