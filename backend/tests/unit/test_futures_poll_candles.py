"""Task 7: `futures_rest_poll_candles` -- the REST-poll candle-source generator.

Deviations from the plan doc's literal Step 1 sketch (all verified empirically
before writing this file, see the PR body for the full explanation):

1. `test_idempotency_replay_same_candle_not_reprocessed` drives `gen1` through
   a SECOND `__anext__()` call before constructing `gen2`. The generator only
   persists the watermark on the call that *resumes* it after the `yield` --
   that's the whole point of the async-generator contract described in the
   plan (watermark advances only once the caller has finished with the
   yielded candle). A generator suspended at its first `yield` and never
   resumed again has not executed the save step at all, so without this
   second call `gen2` would see no persisted watermark and WOULD incorrectly
   re-yield -- the opposite of what this test claims to prove. `test_1`
   above already contains this second call for exactly this reason; `gen1`
   here mirrors it.

2. `test_systematic_failure_escalates_to_error` drives the generator with a
   direct `await gen.__anext__()` instead of `async for _ in gen: pass`.
   `async for` swallows `StopAsyncIteration` internally as its normal-
   termination signal and never re-raises it to the caller, so no
   implementation could ever satisfy `pytest.raises(StopAsyncIteration)`
   wrapped around an `async for` loop over this generator -- confirmed
   empirically on both Python 3.11 (this project's pinned version) and
   3.14. A direct `__anext__()` call is the pattern every other test in
   this file already uses, and is the only one that can observe a clean
   `StopAsyncIteration` from a generator that ends via `return`.

3. `test_gap_detected_and_logged_at_error` uses millisecond-realistic
   `open_time` deltas (seed watermark ~1e9, one skipped 1h candle =
   3_600_000 ms) instead of the plan's tiny synthetic values (1000, 4600,
   8200, 11800). The implementation's `_INTERVAL_SECONDS_MS["1h"] =
   3_600_000` must stay at true Binance millisecond scale to be correct
   against real production data; against that real constant, the plan's
   toy-scale deltas can never trigger the gap branch (the "expected next"
   open_time is always ~3.6M higher than the toy values), so the test could
   never observe the ERROR log it asserts on. Rescaling the fixture --
   not the production constant -- preserves the real-world gap detection
   this feature exists for.
"""
from __future__ import annotations

import logging

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.data.ratelimit import RateLimitedClient, TokenBucket
from app.ws.futures_poll import (
    _CONSECUTIVE_FAILURE_ALERT_THRESHOLD,
    _clear_poll_failure_streaks_for_tests,
    _load_watermark,
    _save_watermark,
    futures_rest_poll_candles,
)

_CREATE_TABLE = (
    "CREATE TABLE live_prediction_watermarks ("
    "symbol TEXT NOT NULL, timeframe TEXT NOT NULL, "
    "last_open_time INTEGER NOT NULL, "
    "updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "PRIMARY KEY (symbol, timeframe))"
)


@pytest.fixture(autouse=True)
def _reset_streaks():
    _clear_poll_failure_streaks_for_tests()
    yield
    _clear_poll_failure_streaks_for_tests()


@pytest.fixture
async def _session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(_CREATE_TABLE))
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _klines_response(rows: list[tuple[int, str]]) -> list[list]:
    # Binance kline row shape: [open_time, open, high, low, close, volume, close_time, ...]
    return [[ot, "100.0", "101.0", "99.0", "100.5", "10.0", ot + 3599999, "0", 0, "0", "0", "0"] for ot, _ in rows]


async def _sleep_once_then_stop(seconds: float) -> None:
    raise StopAsyncIteration  # breaks the poller's while-True after N iterations in tests


def _rate_client(handler) -> RateLimitedClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    return RateLimitedClient(
        exchange="binance_futures", http=http,
        buckets={"default": TokenBucket(capacity=2400.0, refill_per_sec=40.0)},
    )


@pytest.mark.asyncio
async def test_yields_new_closed_candle_and_advances_watermark(_session_factory) -> None:
    call_count = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(200, json=_klines_response([(1000, "a"), (4600, "b")]))

    rate_client = _rate_client(handler)

    gen = futures_rest_poll_candles(
        "SOL/USDT", "1h", rate_client=rate_client, session_factory=_session_factory,
        poll_interval_s=0.0, _sleep=_sleep_once_then_stop,
    )
    candle = await gen.__anext__()
    assert candle.symbol == "SOLUSDT"
    assert candle.close == pytest.approx(100.5)

    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()

    watermark = await _load_watermark(_session_factory, "SOL/USDT", "1h")
    assert watermark == 1000


@pytest.mark.asyncio
async def test_idempotency_replay_same_candle_not_reprocessed(_session_factory) -> None:
    """Required proof obligation: feed the same closed candle twice
    (simulating a restart or overlapping poll) and assert it is NOT
    re-yielded the second time.

    This is the single most important test in this file. See the module
    docstring (deviation 1) for why `gen1` needs a second `__anext__()`
    call before `gen2` is constructed -- without it this test cannot
    actually prove anything about restart safety.
    """
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_klines_response([(1000, "a"), (4600, "b")]))

    rate_client = _rate_client(handler)

    # First generator instance: yields the candle at open_time=1000...
    gen1 = futures_rest_poll_candles(
        "SOL/USDT", "1h", rate_client=rate_client, session_factory=_session_factory,
        poll_interval_s=0.0, _sleep=_sleep_once_then_stop,
    )
    first = await gen1.__anext__()
    assert first is not None
    assert first.symbol == "SOLUSDT"

    # ...then must be RESUMED (not just called once and abandoned) for the
    # watermark save to actually happen -- the save runs in the code path
    # *after* `yield`, which only executes once the caller's loop asks the
    # generator to advance again. This models "downstream processing of the
    # candle completed, the consumer's `async for` loop asks for the next
    # one" -- the real production trigger for the watermark write.
    with pytest.raises(StopAsyncIteration):
        await gen1.__anext__()

    watermark_after_gen1 = await _load_watermark(_session_factory, "SOL/USDT", "1h")
    assert watermark_after_gen1 == 1000, (
        "watermark must be persisted before we can claim gen2 simulates a "
        "restart against real prior state"
    )

    # A second, BRAND NEW generator instance -- simulating a process restart --
    # backed by the SAME session_factory (i.e. the same on-disk/in-memory DB
    # state gen1 left behind). It loads the watermark itself, independently,
    # at construction/first-iteration time; nothing is shared in-process
    # between gen1 and gen2 except the persisted table. It must NOT re-yield
    # open_time=1000, because a real restart would re-poll the exact same
    # exchange data and must not re-signal a candle already processed.
    gen2 = futures_rest_poll_candles(
        "SOL/USDT", "1h", rate_client=rate_client, session_factory=_session_factory,
        poll_interval_s=0.0, _sleep=_sleep_once_then_stop,
    )
    with pytest.raises(StopAsyncIteration):
        await gen2.__anext__()  # no new candle -- watermark already at 1000


@pytest.mark.asyncio
async def test_gap_detected_and_logged_at_error(_session_factory, caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="app.ws.futures_poll")
    interval_ms = 3_600_000
    seed_watermark = 1_000_000_000
    await _save_watermark(_session_factory, "SOL/USDT", "1h", seed_watermark)

    # Skip straight to the candle TWO intervals after the seed -- i.e. skip
    # the one at seed_watermark + interval_ms entirely (a real gap).
    skipped_to = seed_watermark + 2 * interval_ms

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_klines_response([(skipped_to, "x"), (skipped_to + interval_ms, "y")]),
        )

    rate_client = _rate_client(handler)
    gen = futures_rest_poll_candles(
        "SOL/USDT", "1h", rate_client=rate_client, session_factory=_session_factory,
        poll_interval_s=0.0, _sleep=_sleep_once_then_stop,
    )
    candle = await gen.__anext__()  # still yields the newest closed candle (skip-forward)
    assert candle is not None
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert error_records
    assert "gap" in error_records[-1].getMessage().lower()


@pytest.mark.asyncio
async def test_fetch_failure_logs_warning_not_debug(_session_factory, caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="app.ws.futures_poll")

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    rate_client = _rate_client(handler)
    gen = futures_rest_poll_candles(
        "SOL/USDT", "1h", rate_client=rate_client, session_factory=_session_factory,
        poll_interval_s=0.0, _sleep=_sleep_once_then_stop,
    )
    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warning_records
    debug_only_swallows = [
        r for r in caplog.records if r.levelno == logging.DEBUG and "error" in r.getMessage().lower()
    ]
    assert not debug_only_swallows


@pytest.mark.asyncio
async def test_systematic_failure_escalates_to_error(_session_factory, caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="app.ws.futures_poll")

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    rate_client = _rate_client(handler)

    call_count = {"n": 0}

    async def _sleep_n_times_then_stop(seconds: float) -> None:
        call_count["n"] += 1
        if call_count["n"] >= _CONSECUTIVE_FAILURE_ALERT_THRESHOLD:
            raise StopAsyncIteration

    gen = futures_rest_poll_candles(
        "SOL/USDT", "1h", rate_client=rate_client, session_factory=_session_factory,
        poll_interval_s=0.0, _sleep=_sleep_n_times_then_stop,
    )
    # A direct __anext__() call (not `async for`): the poller's internal
    # while-loop never reaches a `yield` on an all-failing transport, so a
    # single call drives all N failed-fetch-then-sleep iterations to
    # completion within one generator resumption, ending in the clean
    # StopAsyncIteration our injected `_sleep` triggers on the Nth call.
    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert error_records
    assert "consecutive" in error_records[-1].getMessage().lower()


@pytest.mark.asyncio
async def test_rate_limit_wait_logged_and_counted(_session_factory, caplog, monkeypatch) -> None:
    caplog.set_level(logging.WARNING, logger="app.ws.futures_poll")

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_klines_response([(1000, "a"), (4600, "b")]))

    rate_client = _rate_client(handler)

    # Force the warning branch deterministically rather than trying to
    # simulate a real slow request: any wait_s > threshold triggers it,
    # so setting the threshold below zero makes even a near-instant
    # mocked-transport round-trip qualify.
    from app.ws import futures_poll as fp_mod
    monkeypatch.setattr(fp_mod, "_RATE_LIMIT_WAIT_LOG_THRESHOLD_S", -1.0)

    gen = futures_rest_poll_candles(
        "SOL/USDT", "1h", rate_client=rate_client, session_factory=_session_factory,
        poll_interval_s=0.0, _sleep=_sleep_once_then_stop,
    )
    await gen.__anext__()
    warning_records = [r for r in caplog.records if "rate-limit wait" in r.getMessage()]
    assert warning_records
    assert fp_mod._RATE_LIMIT_WAIT_COUNT["SOL/USDT"] >= 1
