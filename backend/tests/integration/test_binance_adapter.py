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
    assert c.symbol == "BTC/USDT"
    assert c.timeframe == "1h"
    assert c.ts == datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc)
    assert c.open == 65000.0
    assert c.high == 65500.0
    assert c.low == 64800.0
    assert c.close == 65300.0
    assert c.volume == 1234.56
