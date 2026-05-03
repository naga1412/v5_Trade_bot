import pytest
import respx
import httpx

from app.shadow.universe import fetch_top_n_usdt_futures, AssetUniverseEntry


SAMPLE_24H_RESPONSE = [
    {"symbol": "BTCUSDT", "quoteVolume": "1234567890.0"},
    {"symbol": "ETHUSDT", "quoteVolume": "987654321.0"},
    {"symbol": "SOLUSDT", "quoteVolume": "543210987.0"},
    {"symbol": "BNBUSDT", "quoteVolume": "345678901.0"},
    {"symbol": "XRPUSDT", "quoteVolume": "234567890.0"},
    {"symbol": "BTCUSDC", "quoteVolume": "999999999.0"},  # not USDT, must be excluded
    {"symbol": "DOGEUSDT", "quoteVolume": "123456789.0"},
]


@pytest.mark.asyncio
async def test_fetch_top_n_usdt_futures_returns_sorted_usdt_only() -> None:
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://fapi.binance.com"
    ) as router:
        router.get("/fapi/v1/ticker/24hr").mock(
            return_value=httpx.Response(200, json=SAMPLE_24H_RESPONSE)
        )
        entries = await fetch_top_n_usdt_futures(
            http=http, base_url="https://fapi.binance.com", n=3
        )

    assert len(entries) == 3
    assert all(isinstance(e, AssetUniverseEntry) for e in entries)
    # Sorted by volume desc, USDT only
    assert entries[0].symbol == "BTCUSDT"
    assert entries[0].rank == 1
    assert entries[0].quote_volume_usd_24h == 1234567890.0
    assert entries[1].symbol == "ETHUSDT"
    assert entries[2].symbol == "SOLUSDT"
    # USDC excluded
    assert all(e.symbol.endswith("USDT") for e in entries)
