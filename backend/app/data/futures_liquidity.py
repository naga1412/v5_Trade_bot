"""Phase 4 liquidity floor -- FU-43: 24h volume alone does not imply
resting order-book depth (AKE/APR/CYS/VELVET/BTW all carry $100M-$1.1B
24h volume against under $50k depth within 0.5% of mid). All three
metrics -- volume, spread, depth -- must pass together.

Runs twice: once at daily futures-only universe selection (coarse
inclusion), and again at dispatch time for futures-only-cohort signals
specifically, since books move intraday and a symbol qualifying at
00:00 UTC can be thin hours later when a real signal fires.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.data.ratelimit import RateLimitedClient

_BASE_URL = "https://fapi.binance.com"

QVOL_FLOOR_USDT: float = 20_000_000.0
SPREAD_MAX_BPS: float = 5.0
DEPTH_FLOOR_USDT: float = 50_000.0
_DEPTH_BAND: float = 0.005  # 0.5% of mid, both sides


@dataclass(frozen=True)
class LiquidityCheck:
    passed: bool
    qvol_24h: float
    spread_bps: float
    depth_0_5pct_usdt: float


async def check_liquidity(symbol: str, rate_client: RateLimitedClient) -> LiquidityCheck:
    """Fetch 24h ticker + order-book depth for *symbol* and evaluate the floor.

    Raises on network/parse failure -- callers are responsible for
    catching and logging (see Task 5's daily-selection caller and
    Task 10's dispatch-time caller for the two different failure-
    handling contracts).
    """
    ticker_resp = await rate_client.request(
        "GET", f"{_BASE_URL}/fapi/v1/ticker/24hr",
        endpoint_key="ticker24hr", params={"symbol": symbol}, timeout=10.0,
    )
    ticker_resp.raise_for_status()
    qvol_24h = float(ticker_resp.json()["quoteVolume"])

    depth_resp = await rate_client.request(
        "GET", f"{_BASE_URL}/fapi/v1/depth",
        endpoint_key="depth", params={"symbol": symbol, "limit": "100"}, timeout=10.0,
    )
    depth_resp.raise_for_status()
    book = depth_resp.json()
    bids = [(float(p), float(q)) for p, q in book["bids"]]
    asks = [(float(p), float(q)) for p, q in book["asks"]]

    best_bid, best_ask = bids[0][0], asks[0][0]
    mid = (best_bid + best_ask) / 2
    spread_bps = (best_ask - best_bid) / mid * 10_000

    lo, hi = mid * (1 - _DEPTH_BAND), mid * (1 + _DEPTH_BAND)
    bid_depth = sum(p * q for p, q in bids if p >= lo)
    ask_depth = sum(p * q for p, q in asks if p <= hi)
    depth_0_5pct_usdt = bid_depth + ask_depth

    passed = (
        qvol_24h >= QVOL_FLOOR_USDT
        and spread_bps <= SPREAD_MAX_BPS
        and depth_0_5pct_usdt >= DEPTH_FLOOR_USDT
    )
    return LiquidityCheck(
        passed=passed, qvol_24h=qvol_24h, spread_bps=spread_bps,
        depth_0_5pct_usdt=depth_0_5pct_usdt,
    )


__all__ = ["DEPTH_FLOOR_USDT", "QVOL_FLOOR_USDT", "SPREAD_MAX_BPS", "LiquidityCheck", "check_liquidity"]
