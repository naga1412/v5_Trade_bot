"""TwelveData adapter - direct httpx, free tier 800 calls/day (SP-3 Phase E).

Uses direct httpx (no third-party library) because the free tier surface is
small (just `/time_series`) and async-friendly. Free-tier accounting is a
single `DailyCounterBucket` (800 calls/day, resets 00:00 UTC).

Symbol mapping comes from `app.data.symbols.to_native('twelvedata', ...)`:
FX pairs keep their slash (`EUR/USD` -> `EUR/USD`), stocks/indices pass
through unchanged (`AAPL`), crypto raises `UnknownSymbolError` (not
available on the free tier).

`list_symbols()` always returns `[]` because the symbol-list endpoints
(`/stocks`, `/forex_pairs`, etc.) are paid. The universe must be seeded
manually via `tools/data/seed_twelvedata_symbols.py`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from app.data.adapters._base import Candle, SymbolInfo
from app.data.ratelimit import DailyCounterBucket, RateLimitedClient
from app.data.symbols import to_native


log = logging.getLogger(__name__)


_TF_TO_TWELVEDATA: dict[str, str] = {
    "1m": "1min",
    "5m": "5min",
    "15m": "15min",
    "1h": "1h",
    "4h": "4h",
    "1d": "1day",
}


class TwelveDataError(Exception):
    """TwelveData returned a non-OK status / code in the JSON body."""


def _default_rate_client(http: httpx.AsyncClient) -> RateLimitedClient:
    return RateLimitedClient(
        exchange="twelvedata",
        http=http,
        buckets={"default": DailyCounterBucket(daily_limit=800)},
    )


@dataclass
class TwelveDataAdapter:
    """SP-3 ExchangeAdapter implementation for TwelveData (free tier)."""

    http: httpx.AsyncClient
    apikey: str
    base_url: str = "https://api.twelvedata.com"
    rate_client: RateLimitedClient | None = None
    name: str = field(default="twelvedata", init=False)

    def __post_init__(self) -> None:
        if not self.apikey:
            raise ValueError("TwelveDataAdapter requires non-empty apikey")
        if self.rate_client is None:
            self.rate_client = _default_rate_client(self.http)

    async def fetch_klines(
        self,
        *,
        symbol: str,
        timeframe: str,
        limit: int = 500,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Candle]:
        """Fetch up to `limit` bars for canonical `symbol` at `timeframe`.

        - `symbol` is canonical ('AAPL', 'EUR/USD'); the adapter translates
          via `to_native('twelvedata', ...)`.
        - Crypto pairs raise `UnknownSymbolError` BEFORE any network call.
        - Network/timeout errors return `[]` with a log warning.
        - Body-level error responses (`status == 'error'` or `code` field)
          raise `TwelveDataError`.
        - TwelveData returns newest-first; we reverse so callers get
          oldest-first (matching Bybit/Binance behavior).
        """
        assert self.rate_client is not None
        interval = _TF_TO_TWELVEDATA[timeframe]
        # Map first so unknown symbols raise BEFORE we touch the network.
        native = to_native("twelvedata", symbol)

        params: dict[str, Any] = {
            "symbol": native,
            "interval": interval,
            "outputsize": min(5000, max(1, limit)),
            "apikey": self.apikey,
            "format": "JSON",
        }
        if start is not None:
            params["start_date"] = start.strftime("%Y-%m-%d %H:%M:%S")
        if end is not None:
            params["end_date"] = end.strftime("%Y-%m-%d %H:%M:%S")

        try:
            response = await self.rate_client.request(
                "GET",
                f"{self.base_url}/time_series",
                params=params,
                timeout=10.0,
            )
            response.raise_for_status()
        except (httpx.TimeoutException, httpx.NetworkError) as e:
            log.warning("twelvedata fetch_klines network error: %s", e)
            return []

        body = response.json()
        if body.get("status") == "error" or "code" in body:
            raise TwelveDataError(
                f"{body.get('code', '?')}: {body.get('message', body)}"
            )

        out: list[Candle] = []
        for v in body.get("values") or []:
            ts = _parse_td_datetime(v["datetime"])
            out.append(
                Candle(
                    ts=ts,
                    open=float(v["open"]),
                    high=float(v["high"]),
                    low=float(v["low"]),
                    close=float(v["close"]),
                    volume=float(v.get("volume") or 0.0),
                )
            )
        # TwelveData returns newest-first; reverse for oldest-first.
        out.reverse()
        return out

    async def list_symbols(self) -> list[SymbolInfo]:
        # Spec section 3.4: free-tier symbol-list endpoints are paid. The
        # universe must be seeded manually via
        # tools/data/seed_twelvedata_symbols.py.
        return []


def _parse_td_datetime(s: str) -> datetime:
    """Parse a TwelveData datetime string to a tz-aware UTC datetime.

    TD returns date-only strings for daily bars ("2026-05-01") and ISO-ish
    strings without timezone for intraday ("2026-05-01 14:30:00"). Both
    are interpreted as UTC.
    """
    # Allow space-separated form by normalizing to ISO 8601.
    iso = s.replace(" ", "T")
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
