"""Unit tests for RateLimitedClient (SP-3 Phase B)."""
from datetime import datetime, timezone

import httpx
import pytest
import respx

from app.data.ratelimit import (
    RateLimitedClient,
    RateLimitExceeded,
    TokenBucket,
)


@pytest.mark.asyncio
async def test_default_endpoint_consumes_one_token() -> None:
    bucket = TokenBucket(capacity=5, refill_per_sec=1.0)
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://x.test"
    ) as router:
        router.get("/x").mock(return_value=httpx.Response(200, json={}))
        client = RateLimitedClient(
            exchange="x", http=http, buckets={"default": bucket},
        )
        await client.request("GET", "https://x.test/x", endpoint_key="default")

    assert bucket.tokens == pytest.approx(4.0, abs=0.05)


@pytest.mark.asyncio
async def test_explicit_weight_overrides_default_one() -> None:
    bucket = TokenBucket(capacity=10, refill_per_sec=10.0)
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://x.test"
    ) as router:
        router.get("/x").mock(return_value=httpx.Response(200))
        client = RateLimitedClient(
            exchange="x", http=http, buckets={"default": bucket},
        )
        await client.request("GET", "https://x.test/x", weight=4)
    assert bucket.tokens == pytest.approx(6.0, abs=0.1)


@pytest.mark.asyncio
async def test_endpoint_weights_lookup() -> None:
    """`endpoint_weights={'klines': 2}` -> /klines call drains 2 tokens."""
    bucket = TokenBucket(capacity=10, refill_per_sec=10.0)
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://x.test"
    ) as router:
        router.get("/api/v3/klines").mock(return_value=httpx.Response(200))
        client = RateLimitedClient(
            exchange="binance",
            http=http,
            buckets={"default": bucket},
            endpoint_weights={"klines": 2},
        )
        await client.request(
            "GET", "https://x.test/api/v3/klines", endpoint_key="klines",
        )
    assert bucket.tokens == pytest.approx(8.0, abs=0.1)


@pytest.mark.asyncio
async def test_binance_header_sync_rewinds_bucket() -> None:
    """X-MBX-USED-WEIGHT-1M=900 -> bucket rewinds to capacity-900=300."""
    bucket = TokenBucket(capacity=1200, refill_per_sec=20.0)
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.binance.com"
    ) as router:
        router.get("/api/v3/klines").mock(
            return_value=httpx.Response(
                200,
                headers={"X-MBX-USED-WEIGHT-1M": "900"},
                json=[],
            )
        )
        client = RateLimitedClient(
            exchange="binance",
            http=http,
            buckets={"default": bucket},
            sync_header="X-MBX-USED-WEIGHT-1M",
            sync_capacity=1200.0,
        )
        await client.request(
            "GET", "https://api.binance.com/api/v3/klines", weight=2,
        )
    # After header sync: bucket = 1200 - 900 = 300 (header is authoritative).
    assert bucket.tokens == pytest.approx(300.0, abs=1.0)


@pytest.mark.asyncio
async def test_raise_on_exhaust_when_bucket_empty() -> None:
    bucket = TokenBucket(capacity=2, refill_per_sec=0.0001)
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://x.test"
    ) as router:
        router.get("/x").mock(return_value=httpx.Response(200))
        client = RateLimitedClient(
            exchange="x", http=http, buckets={"default": bucket},
            raise_on_exhaust=True,
        )
        await client.request("GET", "https://x.test/x")
        await client.request("GET", "https://x.test/x")
        with pytest.raises(RateLimitExceeded):
            await client.request("GET", "https://x.test/x")


@pytest.mark.asyncio
async def test_multiple_named_buckets_routed_by_endpoint_key() -> None:
    """Bybit-style: spot + derivs are independent buckets."""
    spot = TokenBucket(capacity=5, refill_per_sec=10.0)
    derivs = TokenBucket(capacity=10, refill_per_sec=10.0)
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://x.test"
    ) as router:
        router.get("/spot").mock(return_value=httpx.Response(200))
        router.get("/derivs").mock(return_value=httpx.Response(200))
        client = RateLimitedClient(
            exchange="bybit", http=http,
            buckets={"default": spot, "spot": spot, "derivs": derivs},
        )
        await client.request("GET", "https://x.test/spot", endpoint_key="spot")
        await client.request("GET", "https://x.test/derivs", endpoint_key="derivs")
    assert spot.tokens == pytest.approx(4.0, abs=0.1)
    assert derivs.tokens == pytest.approx(9.0, abs=0.1)


@pytest.mark.asyncio
async def test_daily_counter_bucket_resets_at_midnight_utc() -> None:
    """TwelveData-style: 800/day, hard reset 00:00 UTC."""
    from app.data.ratelimit import DailyCounterBucket

    # Inject a fixed clock that starts at 23:30 UTC and jumps to 00:01 UTC.
    times = iter([
        datetime(2026, 5, 5, 23, 30, tzinfo=timezone.utc),
        datetime(2026, 5, 5, 23, 30, tzinfo=timezone.utc),
        datetime(2026, 5, 5, 23, 30, tzinfo=timezone.utc),
        datetime(2026, 5, 5, 23, 30, tzinfo=timezone.utc),
        datetime(2026, 5, 6, 0, 1, tzinfo=timezone.utc),
        datetime(2026, 5, 6, 0, 1, tzinfo=timezone.utc),
        datetime(2026, 5, 6, 0, 1, tzinfo=timezone.utc),
    ])
    bucket = DailyCounterBucket(daily_limit=2, _now=lambda: next(times))
    await bucket.acquire(weight=1)
    await bucket.acquire(weight=1)
    # 3rd call would block, but the clock jumped past midnight - should refill.
    await bucket.acquire(weight=1)
    assert bucket.used_today == 1
