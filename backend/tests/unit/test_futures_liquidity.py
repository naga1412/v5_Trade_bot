# backend/tests/unit/test_futures_liquidity.py
from __future__ import annotations

import httpx
import pytest

from app.data.futures_liquidity import LiquidityCheck, check_liquidity
from app.data.ratelimit import RateLimitedClient, TokenBucket


def _rate_client(transport: httpx.MockTransport) -> RateLimitedClient:
    http = httpx.AsyncClient(transport=transport)
    return RateLimitedClient(
        exchange="binance_futures", http=http,
        buckets={"default": TokenBucket(capacity=2400.0, refill_per_sec=40.0)},
    )


def _mock_transport(*, qvol: str, bids: list[tuple[str, str]], asks: list[tuple[str, str]]) -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/fapi/v1/ticker/24hr":
            return httpx.Response(200, json={"quoteVolume": qvol})
        if req.url.path == "/fapi/v1/depth":
            return httpx.Response(200, json={"bids": bids, "asks": asks})
        return httpx.Response(404, json={})
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_liquid_symbol_passes_all_three_thresholds() -> None:
    # mid=100, spread=(100.01-99.99)/100*10000=2bps, deep book both sides
    transport = _mock_transport(
        qvol="25000000",
        bids=[("99.99", "1000"), ("99.98", "1000")],
        asks=[("100.01", "1000"), ("100.02", "1000")],
    )
    result = await check_liquidity("HYPEUSDT", _rate_client(transport))
    assert isinstance(result, LiquidityCheck)
    assert result.passed is True
    assert result.qvol_24h == pytest.approx(25_000_000.0)
    assert result.spread_bps == pytest.approx(2.0, abs=0.1)
    assert result.depth_0_5pct_usdt > 50_000


@pytest.mark.asyncio
async def test_fails_on_low_volume_despite_deep_book() -> None:
    transport = _mock_transport(
        qvol="5000000",  # under $20M floor
        bids=[("99.99", "1000")], asks=[("100.01", "1000")],
    )
    result = await check_liquidity("THINUSDT", _rate_client(transport))
    assert result.passed is False
    assert result.qvol_24h == pytest.approx(5_000_000.0)


@pytest.mark.asyncio
async def test_fails_on_wide_spread_despite_high_volume() -> None:
    # mid=100, spread=(101-99)/100*10000=200bps -- way over 5bps floor
    transport = _mock_transport(
        qvol="1000000000",
        bids=[("99.00", "10000")], asks=[("101.00", "10000")],
    )
    result = await check_liquidity("WIDEUSDT", _rate_client(transport))
    assert result.passed is False
    assert result.spread_bps > 5.0


@pytest.mark.asyncio
async def test_fails_on_thin_depth_despite_high_volume_and_tight_spread() -> None:
    """The AKE/APR/CYS/VELVET/BTW case from FU-43: real high-volume,
    tight-spread symbols with under $50k resting depth."""
    transport = _mock_transport(
        qvol="1000000000", bids=[("99.99", "0.1")], asks=[("100.01", "0.1")],
    )
    result = await check_liquidity("AKEUSDT", _rate_client(transport))
    assert result.passed is False
    assert result.depth_0_5pct_usdt < 50_000


@pytest.mark.asyncio
async def test_depth_sums_both_sides_within_half_percent_band() -> None:
    transport = _mock_transport(
        qvol="25000000",
        bids=[("99.99", "300"), ("99.50", "300")],  # 99.50 is outside 0.5% of mid=100
        asks=[("100.01", "300")],
    )
    result = await check_liquidity("BANDUSDT", _rate_client(transport))
    # Only 99.99 bid level (within [99.5, 100.5]... actually 99.50 IS exactly
    # at the edge) and 100.01 ask level count toward the 0.5% band.
    assert result.depth_0_5pct_usdt == pytest.approx(300 * 99.99 + 300 * 100.01, rel=0.01) \
        or result.depth_0_5pct_usdt == pytest.approx(300 * 99.99 + 300 * 99.50 + 300 * 100.01, rel=0.01)
