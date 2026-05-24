from datetime import datetime, timezone

import httpx
import pytest
import respx

from app.data.adapters.binance import BinanceClient


SAMPLE_KLINE = [
    [
        1777593600000,        # open time ms  (2026-05-01 00:00:00 UTC)
        "65000.00",
        "65500.00",
        "64800.00",
        "65300.00",
        "1234.56",            # volume
        1777597199999,        # close time ms  (2026-05-01 00:59:59 UTC)
        "80502345.00",
        9876,
        "617.28",
        "40251172.50",
        "0",
    ],
]


@pytest.mark.asyncio
async def test_fetch_klines_parses_binance_response() -> None:
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.binance.com"
    ) as router:
        router.get("/api/v3/klines").mock(
            return_value=httpx.Response(200, json=SAMPLE_KLINE)
        )
        client = BinanceClient(http=http, base_url="https://api.binance.com")
        candles = await client.fetch_klines("BTCUSDT", "1h", limit=1)

    assert len(candles) == 1
    c = candles[0]
    # SP-3 Phase B: Candle is bare OHLCV — symbol/timeframe live on the
    # SymbolInfo + caller context now, not on every bar.
    assert c.ts == datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc)
    assert c.open == 65000.0
    assert c.high == 65500.0
    assert c.low == 64800.0
    assert c.close == 65300.0
    assert c.volume == 1234.56


import json

from app.data.adapters.binance import BinanceKlineStream


SAMPLE_WS_MSG = {
    "e": "kline", "E": 1714525200000, "s": "BTCUSDT",
    "k": {
        "t": 1714521600000, "T": 1714525199999, "s": "BTCUSDT",
        "i": "1h", "o": "65000.00", "c": "65300.00", "h": "65500.00",
        "l": "64800.00", "v": "1234.56", "x": True
    }
}


@pytest.mark.asyncio
async def test_kline_stream_parses_closed_candles_only(monkeypatch) -> None:
    msgs = [SAMPLE_WS_MSG, {**SAMPLE_WS_MSG, "k": {**SAMPLE_WS_MSG["k"], "x": False}}]

    async def fake_iter(_url):
        for m in msgs:
            yield json.dumps(m)

    stream = BinanceKlineStream(symbol="BTCUSDT", timeframe="1h", _connect=fake_iter)
    received = []
    async for candle in stream.stream():
        received.append(candle)
        if len(received) == 1:
            break

    assert len(received) == 1
    assert received[0].close == 65300.0


# ────────────────────────────────────────────────────────────────────────────
# PR-BINANCE-WS-OBSERVABILITY (2026-05-24) — log every exception caught by
# the resilient WS loop. Pre-PR the bare-except swallowed all exceptions
# with zero observability, producing silent worker death (live_worker
# stopped twice on 2026-05-24 with no log evidence of WHY).
# ────────────────────────────────────────────────────────────────────────────

import logging


class _FakeWSException(Exception):
    """Distinct exception type so assertions can identify it precisely."""


@pytest.mark.asyncio
async def test_bare_except_logs_first_failure_at_error_level(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """First connect failure must surface at ERROR — a NEW problem
    appearing in a previously-healthy stream needs to be loud enough
    that routine log scanning notices."""
    # Make backoff sleep instant so the test doesn't wait 30 seconds.
    monkeypatch.setattr(
        "app.data.adapters.binance.asyncio.sleep",
        _make_no_sleep(),
    )

    call_count = {"n": 0}

    async def failing_then_break(_url):  # type: ignore[no-untyped-def]
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise _FakeWSException("simulated WS connect failure")
        # On second connect attempt, yield a closed candle then exit so
        # the outer `async for candle in stream.stream()` consumer can
        # break out of the loop deterministically.
        yield json.dumps(SAMPLE_WS_MSG)

    stream = BinanceKlineStream(
        symbol="BTCUSDT", timeframe="1h", _connect=failing_then_break,
    )

    with caplog.at_level(logging.WARNING, logger="app.data.adapters.binance"):
        received = []
        async for candle in stream.stream():
            received.append(candle)
            break  # one closed candle is enough

    # The first failure log MUST exist and MUST be ERROR-level.
    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(error_records) >= 1, (
        f"first WS failure must log at ERROR level; "
        f"captured records: {[(r.levelname, r.message) for r in caplog.records]}"
    )
    msg = error_records[0].getMessage()
    assert "BinanceKlineStream" in msg
    assert "BTCUSDT" in msg
    assert "1h" in msg
    assert "_FakeWSException" in msg
    assert "simulated WS connect failure" in msg
    assert "consecutive=1" in msg
    # We yielded a candle on the second connect attempt — confirms the
    # retry path still functions normally (no behavior change).
    assert len(received) == 1


@pytest.mark.asyncio
async def test_bare_except_logs_subsequent_failures_at_warning_level(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """Once we KNOW about a failure (first log was ERROR), subsequent
    retries during the backoff window should downgrade to WARNING so a
    legitimate transient hiccup doesn't spam ERROR for ~60s."""
    monkeypatch.setattr(
        "app.data.adapters.binance.asyncio.sleep",
        _make_no_sleep(),
    )

    call_count = {"n": 0}

    async def failing_then_succeed(_url):  # type: ignore[no-untyped-def]
        call_count["n"] += 1
        if call_count["n"] <= 4:
            raise _FakeWSException(f"failure #{call_count['n']}")
        # On the 5th connect attempt, yield a candle so the consumer can exit.
        yield json.dumps(SAMPLE_WS_MSG)

    stream = BinanceKlineStream(
        symbol="ETHUSDT", timeframe="15m", _connect=failing_then_succeed,
    )

    with caplog.at_level(logging.WARNING, logger="app.data.adapters.binance"):
        async for _candle in stream.stream():
            break

    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]

    # Exactly ONE ERROR (the first failure), THREE WARNINGs (failures 2-4).
    assert len(error_records) == 1, (
        f"expected exactly one ERROR record (the first failure); got {len(error_records)}"
    )
    assert len(warning_records) == 3, (
        f"expected 3 WARNING records for failures 2-4; got {len(warning_records)}"
    )
    # Verify the WARNING records carry incrementing consecutive counts.
    for i, r in enumerate(warning_records, start=2):
        assert f"consecutive={i}" in r.getMessage()


@pytest.mark.asyncio
async def test_successful_candle_resets_consecutive_failure_counter(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """After a successful candle yield, the consecutive_failures counter
    must reset to 0 — so a NEW failure (after recovery) re-logs at ERROR,
    not WARNING. This prevents 'this is the same problem' from masking
    'this is a NEW problem after recovery'."""
    monkeypatch.setattr(
        "app.data.adapters.binance.asyncio.sleep",
        _make_no_sleep(),
    )

    call_count = {"n": 0}

    async def fail_succeed_fail_succeed(_url):  # type: ignore[no-untyped-def]
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise _FakeWSException("first batch failure")
        if call_count["n"] == 2:
            yield json.dumps(SAMPLE_WS_MSG)
            return  # generator exits → outer loop catches in next iter
        if call_count["n"] == 3:
            raise _FakeWSException("second batch failure — counter should have reset")
        # 4th attempt yields a candle so we can exit cleanly.
        yield json.dumps(SAMPLE_WS_MSG)

    stream = BinanceKlineStream(
        symbol="SOLUSDT", timeframe="5m", _connect=fail_succeed_fail_succeed,
    )

    with caplog.at_level(logging.WARNING, logger="app.data.adapters.binance"):
        received = []
        async for candle in stream.stream():
            received.append(candle)
            if len(received) == 2:
                break

    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    # TWO ERROR records — one for each "first failure" of its window.
    assert len(error_records) == 2, (
        f"expected 2 ERROR records (first failure of each window); "
        f"got {len(error_records)}: "
        f"{[(r.levelname, r.message) for r in caplog.records]}"
    )
    assert "first batch failure" in error_records[0].getMessage()
    assert "second batch failure" in error_records[1].getMessage()
    # Both ERRORs should carry consecutive=1 (counter reset between them).
    for r in error_records:
        assert "consecutive=1" in r.getMessage()


@pytest.mark.asyncio
async def test_no_log_records_when_stream_runs_cleanly(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Regression: when no exception occurs, no log records are emitted
    at ERROR or WARNING by this adapter. Observability must NOT introduce
    log spam on the happy path."""
    async def clean_stream(_url):  # type: ignore[no-untyped-def]
        yield json.dumps(SAMPLE_WS_MSG)

    stream = BinanceKlineStream(
        symbol="BTCUSDT", timeframe="1h", _connect=clean_stream,
    )

    with caplog.at_level(logging.WARNING, logger="app.data.adapters.binance"):
        async for _candle in stream.stream():
            break

    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert error_records == []
    assert warning_records == []


def _make_no_sleep():  # type: ignore[no-untyped-def]
    """Drop-in for asyncio.sleep that returns immediately. Lets the
    backoff-retry loop iterate fast in tests instead of waiting 30s."""
    async def _noop_sleep(_seconds: float) -> None:
        return None
    return _noop_sleep
