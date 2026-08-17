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

from app.data.ratelimit import RateLimitedClient
from app.shadow.multi_stream import MultiStreamCandle

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
