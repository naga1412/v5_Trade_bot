"""Unit tests for TwelveDataAdapter (SP-3 Phase E - >=10 tests)."""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest
import respx

from app.data.adapters._base import ExchangeAdapter
from app.data.adapters.twelvedata import (
    TwelveDataAdapter,
    TwelveDataError,
)


_TS_RESPONSE = {
    "meta": {
        "symbol": "AAPL",
        "interval": "1day",
        "currency": "USD",
        "exchange_timezone": "America/New_York",
        "exchange": "NASDAQ",
        "type": "Common Stock",
    },
    "values": [
        {
            "datetime": "2026-05-01",
            "open": "175.0",
            "high": "178.0",
            "low": "174.5",
            "close": "177.5",
            "volume": "5000000",
        },
    ],
    "status": "ok",
}


@pytest.mark.asyncio
async def test_twelvedata_adapter_satisfies_protocol() -> None:
    async with httpx.AsyncClient() as http:
        adapter = TwelveDataAdapter(http=http, apikey="test-key")
    assert isinstance(adapter, ExchangeAdapter)
    assert adapter.name == "twelvedata"


@pytest.mark.asyncio
async def test_fetch_klines_stock_happy_path() -> None:
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.twelvedata.com",
    ) as router:
        route = router.get("/time_series").mock(
            return_value=httpx.Response(200, json=_TS_RESPONSE),
        )
        adapter = TwelveDataAdapter(http=http, apikey="key123")
        bars = await adapter.fetch_klines(symbol="AAPL", timeframe="1d", limit=1)
    assert "symbol=AAPL" in str(route.calls[0].request.url)
    assert "apikey=key123" in str(route.calls[0].request.url)
    assert "interval=1day" in str(route.calls[0].request.url)
    assert len(bars) == 1
    assert bars[0].close == 177.5
    assert bars[0].volume == 5000000.0
    assert bars[0].ts.tzinfo is not None


@pytest.mark.asyncio
async def test_fetch_klines_fx_keeps_slash() -> None:
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.twelvedata.com",
    ) as router:
        route = router.get("/time_series").mock(
            return_value=httpx.Response(
                200,
                json={
                    **_TS_RESPONSE,
                    "values": [
                        {
                            "datetime": "2026-05-01",
                            "open": "1.10",
                            "high": "1.11",
                            "low": "1.09",
                            "close": "1.105",
                            "volume": "0",
                        },
                    ],
                },
            ),
        )
        adapter = TwelveDataAdapter(http=http, apikey="k")
        bars = await adapter.fetch_klines(
            symbol="EUR/USD", timeframe="1d", limit=1,
        )
    # URL-encoded slash = %2F
    assert "symbol=EUR%2FUSD" in str(route.calls[0].request.url)
    assert bars[0].close == 1.105


@pytest.mark.asyncio
async def test_fetch_klines_empty_values_returns_empty_list() -> None:
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.twelvedata.com",
    ) as router:
        router.get("/time_series").mock(
            return_value=httpx.Response(
                200, json={"values": [], "status": "ok"},
            ),
        )
        adapter = TwelveDataAdapter(http=http, apikey="k")
        bars = await adapter.fetch_klines(symbol="AAPL", timeframe="1d")
    assert bars == []


@pytest.mark.asyncio
async def test_429_response_raises_twelvedata_error() -> None:
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.twelvedata.com",
    ) as router:
        router.get("/time_series").mock(
            return_value=httpx.Response(
                200,
                json={
                    "code": 429,
                    "message": "rate limit exceeded",
                    "status": "error",
                },
            ),
        )
        adapter = TwelveDataAdapter(http=http, apikey="k")
        with pytest.raises(TwelveDataError, match="429"):
            await adapter.fetch_klines(symbol="AAPL", timeframe="1d")


@pytest.mark.asyncio
async def test_network_timeout_returns_empty_list() -> None:
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.twelvedata.com",
    ) as router:
        router.get("/time_series").mock(
            side_effect=httpx.TimeoutException("timeout"),
        )
        adapter = TwelveDataAdapter(http=http, apikey="k")
        bars = await adapter.fetch_klines(symbol="AAPL", timeframe="1d")
    assert bars == []


@pytest.mark.asyncio
async def test_list_symbols_returns_empty() -> None:
    async with httpx.AsyncClient() as http:
        adapter = TwelveDataAdapter(http=http, apikey="k")
        symbols = await adapter.list_symbols()
    assert symbols == []


@pytest.mark.asyncio
async def test_crypto_raises_unknown_symbol_error() -> None:
    from app.data.symbols import UnknownSymbolError

    async with httpx.AsyncClient() as http:
        adapter = TwelveDataAdapter(http=http, apikey="k")
        with pytest.raises(UnknownSymbolError):
            await adapter.fetch_klines(symbol="BTC/USDT", timeframe="1d")


@pytest.mark.asyncio
async def test_daily_counter_drains_on_each_request() -> None:
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.twelvedata.com",
    ) as router:
        router.get("/time_series").mock(
            return_value=httpx.Response(200, json=_TS_RESPONSE),
        )
        adapter = TwelveDataAdapter(http=http, apikey="k")
        assert adapter.rate_client is not None
        bucket = adapter.rate_client.buckets["default"]
        before = bucket.used_today  # type: ignore[attr-defined]
        await adapter.fetch_klines(symbol="AAPL", timeframe="1d")
        after = bucket.used_today  # type: ignore[attr-defined]
    assert after == before + 1


@pytest.mark.asyncio
async def test_daily_counter_exhaustion_raises_when_configured() -> None:
    """When the daily bucket is empty and raise_on_exhaust=True, requests
    are rejected before any network call."""
    from app.data.ratelimit import (
        DailyCounterBucket,
        RateLimitedClient,
        RateLimitExceeded,
    )

    async with httpx.AsyncClient() as http:
        bucket = DailyCounterBucket(daily_limit=1)
        # Pre-drain the bucket.
        await bucket.acquire(weight=1)
        rc = RateLimitedClient(
            exchange="twelvedata",
            http=http,
            buckets={"default": bucket},
            raise_on_exhaust=True,
        )
        adapter = TwelveDataAdapter(http=http, apikey="k", rate_client=rc)
        with pytest.raises(RateLimitExceeded):
            await adapter.fetch_klines(symbol="AAPL", timeframe="1d")


@pytest.mark.asyncio
async def test_missing_apikey_raises_at_construction() -> None:
    async with httpx.AsyncClient() as http:
        with pytest.raises(ValueError, match="apikey"):
            TwelveDataAdapter(http=http, apikey="")


@pytest.mark.asyncio
async def test_fetch_klines_reverses_to_oldest_first() -> None:
    """TwelveData returns newest-first; adapter reverses to oldest-first."""
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.twelvedata.com",
    ) as router:
        router.get("/time_series").mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": "ok",
                    "values": [
                        {
                            "datetime": "2026-05-03",
                            "open": "3", "high": "3", "low": "3",
                            "close": "3", "volume": "0",
                        },
                        {
                            "datetime": "2026-05-02",
                            "open": "2", "high": "2", "low": "2",
                            "close": "2", "volume": "0",
                        },
                        {
                            "datetime": "2026-05-01",
                            "open": "1", "high": "1", "low": "1",
                            "close": "1", "volume": "0",
                        },
                    ],
                },
            ),
        )
        adapter = TwelveDataAdapter(http=http, apikey="k")
        bars = await adapter.fetch_klines(symbol="AAPL", timeframe="1d")
    assert [b.close for b in bars] == [1.0, 2.0, 3.0]
    assert bars[0].ts < bars[-1].ts
    assert bars[0].ts == datetime(2026, 5, 1, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_interval_mapping_for_intraday_timeframes() -> None:
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.twelvedata.com",
    ) as router:
        route = router.get("/time_series").mock(
            return_value=httpx.Response(200, json=_TS_RESPONSE),
        )
        adapter = TwelveDataAdapter(http=http, apikey="k")
        await adapter.fetch_klines(symbol="AAPL", timeframe="5m")
    assert "interval=5min" in str(route.calls[0].request.url)
