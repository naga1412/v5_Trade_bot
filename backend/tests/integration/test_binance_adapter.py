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
