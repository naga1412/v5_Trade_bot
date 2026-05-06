from datetime import timezone

import httpx
import pytest
import respx

from app.data.adapters.binance_futures_intermarket import (
    BinanceFuturesIntermarketAdapter,
    IntermarketSnapshot,
)


_PREMIUM_INDEX_PAYLOAD = {
    "symbol": "BTCUSDT",
    "markPrice": "78320.50",
    "lastFundingRate": "-0.00012",
    "nextFundingTime": 1746547200000,
    "time": 1746540000000,
}

_OI_HIST_PAYLOAD = [
    {
        "symbol": "BTCUSDT",
        "sumOpenInterest": "1.23e9",
        "sumOpenInterestValue": "9.7e13",
        "timestamp": 1746540000000,
    }
]


@pytest.mark.asyncio
async def test_fetch_snapshot_returns_full_record() -> None:
    async with httpx.AsyncClient() as http:
        adapter = BinanceFuturesIntermarketAdapter(http=http)
        with respx.mock(assert_all_called=True) as mock:
            mock.get("https://fapi.binance.com/fapi/v1/premiumIndex").mock(
                return_value=httpx.Response(200, json=_PREMIUM_INDEX_PAYLOAD)
            )
            mock.get("https://fapi.binance.com/fapi/v1/openInterestHist").mock(
                return_value=httpx.Response(200, json=_OI_HIST_PAYLOAD)
            )
            snap = await adapter.fetch_snapshot("BTC/USDT")
    assert isinstance(snap, IntermarketSnapshot)
    assert snap.symbol == "BTC/USDT"
    assert snap.source == "binance_futures"
    assert snap.funding_rate == pytest.approx(-0.00012)
    assert snap.mark_price == pytest.approx(78320.50)
    assert snap.open_interest == pytest.approx(1.23e9)
    assert snap.captured_at.tzinfo is timezone.utc


@pytest.mark.asyncio
async def test_fetch_snapshot_returns_none_on_premium_index_network_error(caplog) -> None:
    async with httpx.AsyncClient() as http:
        adapter = BinanceFuturesIntermarketAdapter(http=http)
        with respx.mock() as mock:
            mock.get("https://fapi.binance.com/fapi/v1/premiumIndex").mock(
                side_effect=httpx.ConnectError("boom")
            )
            snap = await adapter.fetch_snapshot("BTC/USDT")
    assert snap is None
    assert any("binance_futures" in r.message.lower() for r in caplog.records)


@pytest.mark.asyncio
async def test_fetch_snapshot_partial_oi_failure_keeps_funding(caplog) -> None:
    async with httpx.AsyncClient() as http:
        adapter = BinanceFuturesIntermarketAdapter(http=http)
        with respx.mock() as mock:
            mock.get("https://fapi.binance.com/fapi/v1/premiumIndex").mock(
                return_value=httpx.Response(200, json=_PREMIUM_INDEX_PAYLOAD)
            )
            mock.get("https://fapi.binance.com/fapi/v1/openInterestHist").mock(
                side_effect=httpx.ConnectError("oi down")
            )
            snap = await adapter.fetch_snapshot("BTC/USDT")
    assert snap is not None
    assert snap.funding_rate == pytest.approx(-0.00012)
    assert snap.open_interest is None


@pytest.mark.asyncio
async def test_fetch_snapshot_translates_canonical_symbol_to_native() -> None:
    """BTC/USDT must hit the Binance Futures endpoints with symbol=BTCUSDT."""
    async with httpx.AsyncClient() as http:
        adapter = BinanceFuturesIntermarketAdapter(http=http)
        with respx.mock() as mock:
            premium = mock.get(
                "https://fapi.binance.com/fapi/v1/premiumIndex"
            ).mock(return_value=httpx.Response(200, json=_PREMIUM_INDEX_PAYLOAD))
            oi = mock.get(
                "https://fapi.binance.com/fapi/v1/openInterestHist"
            ).mock(return_value=httpx.Response(200, json=_OI_HIST_PAYLOAD))
            await adapter.fetch_snapshot("BTC/USDT")
    assert premium.calls.last.request.url.params["symbol"] == "BTCUSDT"
    assert oi.calls.last.request.url.params["symbol"] == "BTCUSDT"


@pytest.mark.asyncio
async def test_fetch_snapshot_returns_none_on_429() -> None:
    async with httpx.AsyncClient() as http:
        adapter = BinanceFuturesIntermarketAdapter(http=http)
        with respx.mock() as mock:
            mock.get("https://fapi.binance.com/fapi/v1/premiumIndex").mock(
                return_value=httpx.Response(429, headers={"Retry-After": "1"},
                                            json={"code": -1003, "msg": "Too many requests"})
            )
            snap = await adapter.fetch_snapshot("BTC/USDT")
    assert snap is None


@pytest.mark.asyncio
async def test_fetch_snapshot_returns_none_on_invalid_symbol() -> None:
    """Binance Futures: HTTP 400 + {code:-1121, msg:'Invalid symbol.'}."""
    async with httpx.AsyncClient() as http:
        adapter = BinanceFuturesIntermarketAdapter(http=http)
        with respx.mock() as mock:
            mock.get("https://fapi.binance.com/fapi/v1/premiumIndex").mock(
                return_value=httpx.Response(400,
                    json={"code": -1121, "msg": "Invalid symbol."})
            )
            snap = await adapter.fetch_snapshot("FAKECOIN/USDT")
    assert snap is None
