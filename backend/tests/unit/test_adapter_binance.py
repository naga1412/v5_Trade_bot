"""Unit tests for BinanceAdapter (SP-3 Phase B refactor)."""
import httpx
import pytest
import respx

from app.data.adapters._base import ExchangeAdapter
from app.data.adapters.binance import BinanceAdapter


@pytest.mark.asyncio
async def test_binance_adapter_satisfies_protocol() -> None:
    async with httpx.AsyncClient() as http:
        adapter = BinanceAdapter(http=http)
    assert isinstance(adapter, ExchangeAdapter)
    assert adapter.name == "binance"


@pytest.mark.asyncio
async def test_fetch_klines_accepts_canonical_form() -> None:
    """SP-3: callers pass canonical 'BTC/USDT', adapter translates internally."""
    sample = [[
        1777593600000, "65000.00", "65500.00", "64800.00", "65300.00", "1234.56",
        1777597199999, "0", 0, "0", "0", "0",
    ]]
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.binance.com"
    ) as router:
        route = router.get("/api/v3/klines").mock(
            return_value=httpx.Response(200, json=sample)
        )
        adapter = BinanceAdapter(http=http)
        bars = await adapter.fetch_klines(symbol="BTC/USDT", timeframe="1h", limit=1)
    assert route.called
    # The outgoing request used the native form.
    call = route.calls[0]
    assert "symbol=BTCUSDT" in str(call.request.url)
    assert len(bars) == 1
    assert bars[0].close == 65300.0


@pytest.mark.asyncio
async def test_list_symbols_parses_exchangeinfo() -> None:
    sample = {
        "symbols": [
            {
                "symbol": "BTCUSDT", "status": "TRADING",
                "baseAsset": "BTC", "quoteAsset": "USDT",
                "isSpotTradingAllowed": True,
            },
            {
                "symbol": "DELISTED1", "status": "BREAK",
                "baseAsset": "X", "quoteAsset": "USDT",
                "isSpotTradingAllowed": False,
            },
        ],
    }
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.binance.com"
    ) as router:
        router.get("/api/v3/exchangeInfo").mock(
            return_value=httpx.Response(200, json=sample)
        )
        adapter = BinanceAdapter(http=http)
        symbols = await adapter.list_symbols()
    canonicals = {s.canonical for s in symbols}
    assert "BTC/USDT" in canonicals
    assert "X/USDT" not in canonicals  # delisted/non-trading filtered out


@pytest.mark.asyncio
async def test_header_sync_updates_bucket_after_fetch() -> None:
    sample: list = []
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.binance.com"
    ) as router:
        router.get("/api/v3/klines").mock(
            return_value=httpx.Response(
                200,
                headers={"X-MBX-USED-WEIGHT-1M": "1100"},
                json=sample,
            )
        )
        adapter = BinanceAdapter(http=http)
        await adapter.fetch_klines(symbol="BTC/USDT", timeframe="1h", limit=1)
        # Bucket should now reflect 1200 - 1100 = 100 tokens left.
        assert adapter.rate_client is not None
        bucket = adapter.rate_client.buckets["default"]
        assert bucket.tokens == pytest.approx(100.0, abs=2.0)
