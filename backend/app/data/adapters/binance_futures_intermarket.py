"""Binance Futures intermarket adapter (SP-3.5 Phase B1).

Pulls funding rate + mark price from /fapi/v1/premiumIndex and the most
recent 5m open-interest bucket from /fapi/v1/openInterestHist. Returns a
single :class:`IntermarketSnapshot` per ``fetch_snapshot()`` call.

Each snapshot costs 2 weight against the Binance Futures 2400/min bucket;
top-30 universe at 5min cadence = 60 weight per tick = ~720/hr.

Failure modes:
* premiumIndex network error → ``None`` returned (caller skips the symbol).
* openInterestHist network error → snapshot returned with ``open_interest=None``
  (funding decay trap can still fire; squeeze cascade trap silently abstains).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from app.data.ratelimit import RateLimitedClient, TokenBucket


log = logging.getLogger(__name__)

_BASE_URL = "https://fapi.binance.com"
_PREMIUM_INDEX = "/fapi/v1/premiumIndex"
_OI_HIST = "/fapi/v1/openInterestHist"


@dataclass(frozen=True)
class IntermarketSnapshot:
    """One funding-rate + OI sample for a single symbol at a single instant."""

    symbol: str
    captured_at: datetime
    funding_rate: float | None
    mark_price: float | None
    open_interest: float | None
    source: str  # "binance_futures" | "bybit"


def _to_native(canonical: str) -> str:
    """BTC/USDT → BTCUSDT. Pass-through if already native."""
    return canonical.replace("/", "") if "/" in canonical else canonical


def _default_rate_client(http: httpx.AsyncClient) -> RateLimitedClient:
    return RateLimitedClient(
        exchange="binance_futures",
        http=http,
        buckets={"default": TokenBucket(capacity=2400, refill_per_sec=40.0)},
        endpoint_weights={"premiumIndex": 1, "openInterestHist": 1},
        sync_header="X-MBX-USED-WEIGHT-1M",
        sync_capacity=2400.0,
    )


@dataclass
class BinanceFuturesIntermarketAdapter:
    """SP-3.5 intermarket adapter for Binance Futures."""

    http: httpx.AsyncClient | None = None
    base_url: str = _BASE_URL
    rate_client: RateLimitedClient | None = None
    name: str = field(default="binance_futures", init=False)

    def __post_init__(self) -> None:
        if self.http is None:
            self.http = httpx.AsyncClient()
        if self.rate_client is None:
            self.rate_client = _default_rate_client(self.http)

    async def fetch_snapshot(self, symbol: str) -> IntermarketSnapshot | None:
        assert self.rate_client is not None
        native = _to_native(symbol)

        # 1) premiumIndex — funding rate + mark price.
        try:
            resp_pi = await self.rate_client.request(
                "GET", f"{self.base_url}{_PREMIUM_INDEX}",
                endpoint_key="premiumIndex",
                params={"symbol": native}, timeout=10.0,
            )
            resp_pi.raise_for_status()
            pi = resp_pi.json()
        except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPError) as e:
            log.warning("binance_futures premiumIndex error: %s", e)
            return None

        try:
            funding = float(pi["lastFundingRate"])
            mark = float(pi["markPrice"])
        except (KeyError, TypeError, ValueError) as e:
            log.warning("binance_futures premiumIndex malformed: %s", e)
            return None

        # 2) openInterestHist — most recent 5m bucket.
        oi_value: float | None = None
        try:
            resp_oi = await self.rate_client.request(
                "GET", f"{self.base_url}{_OI_HIST}",
                endpoint_key="openInterestHist",
                params={"symbol": native, "period": "5m", "limit": 1},
                timeout=10.0,
            )
            resp_oi.raise_for_status()
            oi_rows = resp_oi.json()
            if oi_rows:
                oi_value = float(oi_rows[0]["sumOpenInterest"])
        except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPError,
                KeyError, TypeError, ValueError, IndexError) as e:
            log.warning("binance_futures openInterestHist error: %s", e)
            # Continue — partial snapshot is still useful for funding-decay trap.

        return IntermarketSnapshot(
            symbol=symbol,
            captured_at=datetime.now(timezone.utc),
            funding_rate=funding,
            mark_price=mark,
            open_interest=oi_value,
            source="binance_futures",
        )
