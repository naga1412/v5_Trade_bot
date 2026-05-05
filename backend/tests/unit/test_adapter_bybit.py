"""Unit tests for BybitAdapter (SP-3 Phase C - >=10 tests)."""
from datetime import datetime, timezone

import httpx
import pytest
import respx

from app.data.adapters._base import ExchangeAdapter
from app.data.adapters.bybit import BybitAdapter


SAMPLE_KLINE_RESPONSE = {
    "retCode": 0, "retMsg": "OK",
    "result": {
        "category": "spot", "symbol": "BTCUSDT",
        "list": [
            ["1777593600000", "65000.0", "65500.0", "64800.0", "65300.0",
             "1234.5", "80000000"],
        ],
    },
}

SAMPLE_INSTRUMENTS_SPOT = {
    "retCode": 0, "result": {"category": "spot", "list": [
        {"symbol": "BTCUSDT", "baseCoin": "BTC", "quoteCoin": "USDT",
         "status": "Trading"},
        {"symbol": "DELISTED", "baseCoin": "X", "quoteCoin": "USDT",
         "status": "Closed"},
    ]},
}

SAMPLE_INSTRUMENTS_LINEAR = {
    "retCode": 0, "result": {"category": "linear", "list": [
        {"symbol": "ETHUSDT", "baseCoin": "ETH", "quoteCoin": "USDT",
         "status": "Trading", "contractType": "LinearPerpetual"},
    ]},
}


@pytest.mark.asyncio
async def test_bybit_adapter_satisfies_protocol() -> None:
    async with httpx.AsyncClient() as http:
        adapter = BybitAdapter(http=http)
    assert isinstance(adapter, ExchangeAdapter)
    assert adapter.name == "bybit"


@pytest.mark.asyncio
async def test_fetch_klines_happy_path() -> None:
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.bybit.com"
    ) as router:
        router.get("/v5/market/kline").mock(
            return_value=httpx.Response(200, json=SAMPLE_KLINE_RESPONSE)
        )
        adapter = BybitAdapter(http=http)
        bars = await adapter.fetch_klines(
            symbol="BTC/USDT", timeframe="1h", limit=1,
        )
    assert len(bars) == 1
    b = bars[0]
    assert b.ts == datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc)
    assert b.open == 65000.0 and b.close == 65300.0
    assert b.volume == 1234.5


@pytest.mark.asyncio
async def test_fetch_klines_empty_response_returns_empty_list() -> None:
    empty = {"retCode": 0, "result": {"category": "spot",
                                      "symbol": "BTCUSDT", "list": []}}
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.bybit.com"
    ) as router:
        router.get("/v5/market/kline").mock(
            return_value=httpx.Response(200, json=empty)
        )
        adapter = BybitAdapter(http=http)
        bars = await adapter.fetch_klines(symbol="BTC/USDT", timeframe="1h")
    assert bars == []


@pytest.mark.asyncio
async def test_fetch_klines_network_timeout_returns_empty() -> None:
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.bybit.com"
    ) as router:
        router.get("/v5/market/kline").mock(
            side_effect=httpx.TimeoutException("simulated timeout")
        )
        adapter = BybitAdapter(http=http)
        bars = await adapter.fetch_klines(symbol="BTC/USDT", timeframe="1h")
    assert bars == []


@pytest.mark.asyncio
async def test_fetch_klines_non_zero_retcode_raises() -> None:
    err = {"retCode": 10001, "retMsg": "params error"}
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.bybit.com"
    ) as router:
        router.get("/v5/market/kline").mock(
            return_value=httpx.Response(200, json=err)
        )
        adapter = BybitAdapter(http=http)
        with pytest.raises(Exception):
            await adapter.fetch_klines(symbol="BAD/USDT", timeframe="1h")


@pytest.mark.asyncio
async def test_list_symbols_returns_spot_and_linear_combined() -> None:
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.bybit.com"
    ) as router:
        router.get(
            "/v5/market/instruments-info", params={"category": "spot"},
        ).mock(
            return_value=httpx.Response(200, json=SAMPLE_INSTRUMENTS_SPOT)
        )
        router.get(
            "/v5/market/instruments-info", params={"category": "linear"},
        ).mock(
            return_value=httpx.Response(200, json=SAMPLE_INSTRUMENTS_LINEAR)
        )
        adapter = BybitAdapter(http=http)
        symbols = await adapter.list_symbols()
    canonicals = {s.canonical for s in symbols}
    assert "BTC/USDT" in canonicals
    assert "ETH/USDT" in canonicals
    # Closed instruments filtered out
    assert all(s.canonical != "X/USDT" for s in symbols)


@pytest.mark.asyncio
async def test_list_symbols_filters_non_trading_status() -> None:
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.bybit.com"
    ) as router:
        router.get(
            "/v5/market/instruments-info", params={"category": "spot"},
        ).mock(
            return_value=httpx.Response(200, json=SAMPLE_INSTRUMENTS_SPOT)
        )
        router.get(
            "/v5/market/instruments-info", params={"category": "linear"},
        ).mock(
            return_value=httpx.Response(
                200, json={"retCode": 0, "result": {"list": []}},
            )
        )
        adapter = BybitAdapter(http=http)
        symbols = await adapter.list_symbols()
    assert {s.canonical for s in symbols} == {"BTC/USDT"}


@pytest.mark.asyncio
async def test_dual_buckets_routed_correctly() -> None:
    """Spot endpoint drains spot bucket; perp endpoint drains derivs bucket."""
    from app.data.ratelimit import RateLimitedClient

    class _CountingBucket:
        def __init__(self) -> None:
            self.acquired = 0.0

        @property
        def tokens(self) -> float:
            return 1000.0  # never empty

        async def acquire(self, weight: float = 1.0) -> None:
            self.acquired += weight

    spot_bucket = _CountingBucket()
    derivs_bucket = _CountingBucket()
    default_bucket = _CountingBucket()

    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.bybit.com"
    ) as router:
        router.get("/v5/market/kline").mock(
            return_value=httpx.Response(200, json=SAMPLE_KLINE_RESPONSE)
        )
        rate_client = RateLimitedClient(
            exchange="bybit",
            http=http,
            buckets={
                "default": default_bucket,
                "spot": spot_bucket,
                "derivs": derivs_bucket,
            },
        )
        adapter = BybitAdapter(http=http, rate_client=rate_client)

        await adapter.fetch_klines(
            symbol="BTC/USDT", timeframe="1h", _category="spot",
        )
        await adapter.fetch_klines(
            symbol="ETH/USDT", timeframe="1h", _category="linear",
        )

    assert spot_bucket.acquired > 0
    assert derivs_bucket.acquired > 0
    # Default must NOT be touched — spot/linear route to their dedicated buckets.
    assert default_bucket.acquired == 0


@pytest.mark.parametrize("tf, expected", [
    ("1m", "1"), ("5m", "5"), ("15m", "15"),
    ("1h", "60"), ("4h", "240"), ("1d", "D"),
])
@pytest.mark.asyncio
async def test_timeframe_mapping(tf: str, expected: str) -> None:
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.bybit.com"
    ) as router:
        route = router.get("/v5/market/kline").mock(
            return_value=httpx.Response(200, json=SAMPLE_KLINE_RESPONSE)
        )
        adapter = BybitAdapter(http=http)
        await adapter.fetch_klines(symbol="BTC/USDT", timeframe=tf, limit=1)
    assert f"interval={expected}" in str(route.calls[0].request.url)


@pytest.mark.asyncio
async def test_fetch_klines_passes_start_end_as_ms() -> None:
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.bybit.com"
    ) as router:
        route = router.get("/v5/market/kline").mock(
            return_value=httpx.Response(200, json=SAMPLE_KLINE_RESPONSE)
        )
        adapter = BybitAdapter(http=http)
        await adapter.fetch_klines(
            symbol="BTC/USDT", timeframe="1h",
            start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
    url = str(route.calls[0].request.url)
    assert "start=" in url and "end=" in url


@pytest.mark.asyncio
async def test_to_native_from_native_round_trip() -> None:
    """Bybit reuses the Binance crypto branch in symbols.py."""
    from app.data.symbols import from_native, to_native

    assert to_native("bybit", "BTC/USDT") == "BTCUSDT"
    assert from_native("bybit", "BTCUSDT") == "BTC/USDT"
    assert to_native("bybit", "ETH/USDC") == "ETHUSDC"
    assert from_native("bybit", "ETHUSDC") == "ETH/USDC"


@pytest.mark.asyncio
async def test_list_symbols_marks_asset_class_crypto() -> None:
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.bybit.com"
    ) as router:
        router.get(
            "/v5/market/instruments-info", params={"category": "spot"},
        ).mock(
            return_value=httpx.Response(200, json=SAMPLE_INSTRUMENTS_SPOT)
        )
        router.get(
            "/v5/market/instruments-info", params={"category": "linear"},
        ).mock(
            return_value=httpx.Response(200, json=SAMPLE_INSTRUMENTS_LINEAR)
        )
        adapter = BybitAdapter(http=http)
        symbols = await adapter.list_symbols()
    assert symbols, "expected at least one symbol"
    assert all(s.asset_class == "crypto" for s in symbols)


@pytest.mark.asyncio
async def test_klines_reversed_to_oldest_first() -> None:
    """Bybit returns newest-first; adapter must reverse."""
    multi = {
        "retCode": 0, "result": {"category": "spot", "symbol": "BTCUSDT",
                                 "list": [
            ["1777597200000", "65300", "65400", "65200", "65350", "10", "0"],
            ["1777593600000", "65000", "65500", "64800", "65300", "20", "0"],
        ]},
    }
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.bybit.com"
    ) as router:
        router.get("/v5/market/kline").mock(
            return_value=httpx.Response(200, json=multi)
        )
        adapter = BybitAdapter(http=http)
        bars = await adapter.fetch_klines(
            symbol="BTC/USDT", timeframe="1h", limit=2,
        )
    assert len(bars) == 2
    assert bars[0].ts < bars[1].ts
