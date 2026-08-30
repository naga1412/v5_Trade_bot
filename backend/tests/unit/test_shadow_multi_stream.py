import json

import pytest

from app.shadow.multi_stream import MultiStreamCandle, MultiStreamReader, build_combined_stream_url


def test_combined_stream_url_lowercase_and_joined() -> None:
    url = build_combined_stream_url(
        symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        timeframe="1h",
        base="wss://fstream.binance.com",
    )
    assert url == "wss://fstream.binance.com/stream?streams=btcusdt@kline_1h/ethusdt@kline_1h/solusdt@kline_1h"


def test_combined_stream_url_default_base_is_spot() -> None:
    """Regression guard: default WS base must be SPOT, never Futures.

    Binance silently geoblocks Futures market-data WS streams from EEA
    jurisdictions (confirmed from Hetzner Helsinki on 2026-05-11 — handshake
    OK, zero frames delivered). The shadow worker MUST default to SPOT WS
    (stream.binance.com:9443) or the worker silently processes zero candles
    in production. See ops-debug ws-probe history + multi_stream.py for the
    full root-cause writeup.
    """
    url = build_combined_stream_url(symbols=["BTCUSDT"], timeframe="1h")
    assert url.startswith("wss://stream.binance.com:9443"), (
        f"default WS base regressed to non-SPOT: {url}. "
        "EEA Futures geoblock will silently kill the shadow worker."
    )
    assert "fstream.binance.com" not in url


def test_multi_stream_reader_default_base_is_spot() -> None:
    """Same regression guard at the reader-init layer."""
    reader = MultiStreamReader(symbols=["BTCUSDT"], timeframe="1h")
    assert reader.url.startswith("wss://stream.binance.com:9443"), (
        f"MultiStreamReader default URL regressed: {reader.url}"
    )


def test_combined_stream_url_empty_symbols_raises() -> None:
    with pytest.raises(ValueError):
        build_combined_stream_url(symbols=[], timeframe="1h")


def test_combined_stream_url_chunked_at_200_streams() -> None:
    """Binance limit is ~1024 streams per connection but Path length matters."""
    symbols = [f"SYM{i}USDT" for i in range(250)]
    url = build_combined_stream_url(symbols=symbols, timeframe="1h", max_streams=200)
    assert url.count("/") - 2 == 200  # 200 streams in URL (subtract scheme //)


def test_combined_stream_url_truncation_logs_loudly(caplog) -> None:
    """2026-08-30 operator ruling: truncation must never be silent -- a
    prior version dropped symbols past max_streams with zero signal,
    and since callers pass an alphabetically-sorted list (worker.py's
    _compute_subscription_set), the drop was not random -- it
    systematically favored early-alphabet tickers. Now it logs at
    ERROR with the exact dropped-symbol list."""
    import logging
    symbols = [f"SYM{i:03d}USDT" for i in range(5)]
    with caplog.at_level(logging.ERROR, logger="app.shadow.multi_stream"):
        build_combined_stream_url(symbols=symbols, timeframe="1h", max_streams=3)
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.ERROR
    assert "TRUNCATING" in caplog.records[0].message
    assert "SYM003USDT" in caplog.records[0].message
    assert "SYM004USDT" in caplog.records[0].message


def test_combined_stream_url_no_truncation_no_log(caplog) -> None:
    """Companion negative case: under the cap, zero log noise."""
    import logging
    with caplog.at_level(logging.ERROR, logger="app.shadow.multi_stream"):
        build_combined_stream_url(symbols=["BTCUSDT", "ETHUSDT"], timeframe="1h", max_streams=200)
    assert len(caplog.records) == 0


def test_multi_stream_reader_exposes_truncated_symbols() -> None:
    """The async caller (worker.py's _build_default_worker) needs this
    to route a truncation through alert_admin -- build_combined_stream_url
    itself stays sync and can only log."""
    symbols = [f"SYM{i:03d}USDT" for i in range(5)]
    reader = MultiStreamReader(symbols=symbols, timeframe="1h", max_streams=3)
    assert reader.truncated_symbols == ["SYM003USDT", "SYM004USDT"]


def test_multi_stream_reader_no_truncation_empty_list() -> None:
    reader = MultiStreamReader(symbols=["BTCUSDT", "ETHUSDT"], timeframe="1h", max_streams=200)
    assert reader.truncated_symbols == []


SAMPLE_CLOSED = {
    "stream": "btcusdt@kline_1h",
    "data": {
        "e": "kline", "E": 1714525200000, "s": "BTCUSDT",
        "k": {"t": 1714521600000, "T": 1714525199999, "s": "BTCUSDT",
              "i": "1h", "o": "65000", "c": "65300", "h": "65500",
              "l": "64800", "v": "1234.56", "x": True}
    }
}
SAMPLE_OPEN = {
    "stream": "ethusdt@kline_1h",
    "data": {
        "e": "kline", "E": 1714525000000, "s": "ETHUSDT",
        "k": {"t": 1714521600000, "T": 1714525199999, "s": "ETHUSDT",
              "i": "1h", "o": "3950", "c": "3960", "h": "3970",
              "l": "3940", "v": "100", "x": False}  # not closed
    }
}


@pytest.mark.asyncio
async def test_multi_stream_yields_closed_only() -> None:
    msgs = [SAMPLE_OPEN, SAMPLE_CLOSED, SAMPLE_OPEN]

    async def fake_connect(_url: str):  # type: ignore[return]
        for m in msgs:
            yield json.dumps(m)

    reader = MultiStreamReader(
        symbols=["BTCUSDT", "ETHUSDT"], timeframe="1h", _connect=fake_connect
    )
    received: list[MultiStreamCandle] = []
    async for candle in reader.stream():
        received.append(candle)
        if len(received) == 1:
            break

    assert len(received) == 1
    assert received[0].symbol == "BTCUSDT"
    assert received[0].close == 65300.0
