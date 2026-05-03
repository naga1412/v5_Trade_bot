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


def test_combined_stream_url_empty_symbols_raises() -> None:
    with pytest.raises(ValueError):
        build_combined_stream_url(symbols=[], timeframe="1h")


def test_combined_stream_url_chunked_at_200_streams() -> None:
    """Binance limit is ~1024 streams per connection but Path length matters."""
    symbols = [f"SYM{i}USDT" for i in range(250)]
    url = build_combined_stream_url(symbols=symbols, timeframe="1h", max_streams=200)
    assert url.count("/") - 2 == 200  # 200 streams in URL (subtract scheme //)


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
