"""Bybit v5 REST adapter (SP-3 Phase C).

Uses direct httpx (not the pybit library — kept pinned as a fallback only)
because v5 endpoints are simple JSON and async-friendly. Dual rate-limit
buckets: `spot` (120 req/sec) and `derivs` (600 req/5sec ≈ 120/sec average).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from app.data.adapters._base import Candle, SymbolInfo
from app.data.ratelimit import RateLimitedClient, TokenBucket
from app.data.symbols import from_native, to_native


log = logging.getLogger(__name__)


_TF_TO_BYBIT = {
    "1m": "1", "5m": "5", "15m": "15",
    "1h": "60", "4h": "240", "1d": "D",
}


def _default_rate_client(http: httpx.AsyncClient) -> RateLimitedClient:
    return RateLimitedClient(
        exchange="bybit",
        http=http,
        buckets={
            "default": TokenBucket(capacity=120, refill_per_sec=120.0),
            "spot": TokenBucket(capacity=120, refill_per_sec=120.0),
            "derivs": TokenBucket(capacity=600, refill_per_sec=120.0),
        },
    )


class BybitError(Exception):
    """Bybit returned a non-zero retCode."""


@dataclass
class BybitAdapter:
    """SP-3 ExchangeAdapter implementation for Bybit (spot + linear perps)."""

    http: httpx.AsyncClient
    base_url: str = "https://api.bybit.com"
    rate_client: RateLimitedClient | None = None
    name: str = field(default="bybit", init=False)

    def __post_init__(self) -> None:
        if self.rate_client is None:
            self.rate_client = _default_rate_client(self.http)

    async def fetch_klines(
        self, *, symbol: str, timeframe: str,
        limit: int = 500,
        start: datetime | None = None, end: datetime | None = None,
        _category: str = "spot",
    ) -> list[Candle]:
        """Fetch up to `limit` bars for `symbol` at `timeframe`.

        `symbol` is canonical 'BTC/USDT'; the adapter translates internally.
        `_category` selects the Bybit product (`spot` or `linear`); choice
        also routes the request to the matching rate-limit bucket.
        """
        assert self.rate_client is not None
        bybit_tf = _TF_TO_BYBIT[timeframe]
        native = to_native("bybit", symbol)
        params: dict[str, Any] = {
            "category": _category,
            "symbol": native,
            "interval": bybit_tf,
            "limit": limit,
        }
        if start is not None:
            params["start"] = int(start.timestamp() * 1000)
        if end is not None:
            params["end"] = int(end.timestamp() * 1000)
        try:
            response = await self.rate_client.request(
                "GET",
                f"{self.base_url}/v5/market/kline",
                endpoint_key=("derivs" if _category == "linear" else "spot"),
                params=params,
                timeout=10.0,
            )
            response.raise_for_status()
        except (httpx.TimeoutException, httpx.NetworkError) as e:
            log.warning("bybit fetch_klines network error: %s", e)
            return []

        body = response.json()
        if body.get("retCode") != 0:
            raise BybitError(f"{body.get('retCode')}: {body.get('retMsg')}")

        rows = body.get("result", {}).get("list") or []
        out: list[Candle] = []
        for row in rows:
            out.append(
                Candle(
                    ts=datetime.fromtimestamp(int(row[0]) / 1000, tz=timezone.utc),
                    open=float(row[1]), high=float(row[2]),
                    low=float(row[3]),  close=float(row[4]),
                    volume=float(row[5]),
                )
            )
        # Bybit returns newest-first; reverse so callers get oldest-first.
        out.reverse()
        return out

    async def list_symbols(self) -> list[SymbolInfo]:
        """Return spot + linear-perpetual symbols (status == Trading)."""
        assert self.rate_client is not None
        all_symbols: list[SymbolInfo] = []
        for category in ("spot", "linear"):
            try:
                response = await self.rate_client.request(
                    "GET",
                    f"{self.base_url}/v5/market/instruments-info",
                    endpoint_key=("derivs" if category == "linear" else "spot"),
                    params={"category": category},
                    timeout=15.0,
                )
                response.raise_for_status()
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                log.warning(
                    "bybit list_symbols(%s) network error: %s", category, e,
                )
                continue
            body = response.json()
            if body.get("retCode") != 0:
                continue
            for inst in body.get("result", {}).get("list", []):
                if inst.get("status") != "Trading":
                    continue
                native = inst.get("symbol", "")
                try:
                    canonical = from_native("bybit", native)
                except Exception:  # noqa: BLE001
                    continue
                all_symbols.append(SymbolInfo(
                    canonical=canonical,
                    native=native,
                    base=inst.get("baseCoin", ""),
                    quote=inst.get("quoteCoin", ""),
                    listed_at=None,
                    delisted_at=None,
                    asset_class="crypto",
                ))
        return all_symbols
