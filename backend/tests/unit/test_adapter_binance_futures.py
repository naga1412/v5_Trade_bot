"""SP-9 — BinanceFuturesAdapter unit tests."""
import httpx
import pytest
import respx

from app.data.adapters._base import ExchangeAdapter
from app.data.adapters.binance import BinanceFuturesAdapter


@pytest.mark.asyncio
async def test_satisfies_protocol_with_distinct_name() -> None:
    async with httpx.AsyncClient() as http:
        adapter = BinanceFuturesAdapter(http=http)
    assert isinstance(adapter, ExchangeAdapter)
    # Distinct name so universe_history rows don't collide with spot.
    assert adapter.name == "binance-futures"


@pytest.mark.asyncio
async def test_list_symbols_filters_to_perpetuals_only() -> None:
    sample = {
        "symbols": [
            {"symbol": "BTCUSDT", "status": "TRADING",
             "contractType": "PERPETUAL", "baseAsset": "BTC", "quoteAsset": "USDT"},
            {"symbol": "SHIBUSDT", "status": "TRADING",
             "contractType": "PERPETUAL", "baseAsset": "SHIB", "quoteAsset": "USDT"},
            # Excluded: not TRADING
            {"symbol": "OLDUSDT", "status": "BREAK",
             "contractType": "PERPETUAL", "baseAsset": "OLD", "quoteAsset": "USDT"},
            # Excluded: dated future, not perpetual
            {"symbol": "BTCUSDT_240927", "status": "TRADING",
             "contractType": "CURRENT_QUARTER",
             "baseAsset": "BTC", "quoteAsset": "USDT"},
        ],
    }
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://fapi.binance.com",
    ) as router:
        router.get("/fapi/v1/exchangeInfo").mock(
            return_value=httpx.Response(200, json=sample),
        )
        adapter = BinanceFuturesAdapter(http=http)
        symbols = await adapter.list_symbols()

    canonicals = {s.canonical for s in symbols}
    assert canonicals == {"BTC/USDT", "SHIB/USDT"}
    btc = next(s for s in symbols if s.canonical == "BTC/USDT")
    assert btc.native == "BTCUSDT"
    assert btc.base == "BTC"
    assert btc.quote == "USDT"
    assert btc.asset_class == "crypto"


@pytest.mark.asyncio
async def test_list_symbols_returns_empty_on_network_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://fapi.binance.com",
    ) as router:
        router.get("/fapi/v1/exchangeInfo").mock(
            side_effect=httpx.NetworkError("boom"),
        )
        adapter = BinanceFuturesAdapter(http=http)
        out = await adapter.list_symbols()
    assert out == []


@pytest.mark.asyncio
async def test_fetch_klines_uses_futures_endpoint() -> None:
    sample = [[
        1777593600000, "65000", "65500", "64800", "65300", "1234.56",
        1777597199999, "0", 0, "0", "0", "0",
    ]]
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://fapi.binance.com",
    ) as router:
        route = router.get("/fapi/v1/klines").mock(
            return_value=httpx.Response(200, json=sample),
        )
        adapter = BinanceFuturesAdapter(http=http)
        bars = await adapter.fetch_klines(
            symbol="BTC/USDT", timeframe="1h", limit=1,
        )
    assert route.called
    assert len(bars) == 1
    assert bars[0].close == 65300


@pytest.mark.asyncio
async def test_adapter_registered_in_factory() -> None:
    """The new adapter must show up in the registry so universe_sync sees it."""
    from app.data.adapters import get_adapter, list_registered
    assert "binance-futures" in list_registered()
    a = get_adapter("binance-futures")
    assert isinstance(a, BinanceFuturesAdapter)
