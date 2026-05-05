"""Yahoo Finance adapter via yfinance (SP-3 Phase D).

yfinance is sync — we wrap calls in asyncio.to_thread so the rest of the app
stays async. Self-throttle is 1 req/sec via a TokenBucket (Yahoo has no
published rate limit; we keep it conservative to avoid the unofficial
throttle).

list_symbols() returns []. Universe must be seeded manually via
tools/data/seed_yahoo_symbols.py.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

import httpx
import pandas as pd

from app.data.adapters._base import Candle, SymbolInfo
from app.data.ratelimit import RateLimitedClient, TokenBucket
from app.data.symbols import to_native


log = logging.getLogger(__name__)


_TF_TO_YAHOO_INTERVAL: dict[str, str] = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "1h": "60m",
    "1d": "1d",
    # "4h" intentionally absent — yfinance does not expose 4-hour bars.
}


def _default_yfinance_download() -> Callable[..., pd.DataFrame]:
    import yfinance  # local import — heavy

    return yfinance.download


def _default_rate_client(http: httpx.AsyncClient) -> RateLimitedClient:
    return RateLimitedClient(
        exchange="yahoo",
        http=http,
        buckets={"default": TokenBucket(capacity=1, refill_per_sec=1.0)},
    )


@dataclass
class YahooAdapter:
    """SP-3 ExchangeAdapter implementation for Yahoo Finance via yfinance."""

    http: httpx.AsyncClient | None = None
    rate_client: RateLimitedClient | None = None
    _download: Callable[..., pd.DataFrame] | None = None
    name: str = field(default="yahoo", init=False)

    def __post_init__(self) -> None:
        if self.http is None:
            self.http = httpx.AsyncClient()
        if self.rate_client is None:
            self.rate_client = _default_rate_client(self.http)
        if self._download is None:
            self._download = _default_yfinance_download()

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

        Validates the timeframe against `_TF_TO_YAHOO_INTERVAL` first
        (raises ValueError for unsupported), then maps the symbol via
        `to_native("yahoo", ...)` (raises UnknownSymbolError before any
        network call), then drains a token from the rate bucket and runs
        the sync `yfinance.download` call off-loop via `asyncio.to_thread`.
        """
        if timeframe not in _TF_TO_YAHOO_INTERVAL:
            raise ValueError(
                f"yfinance does not support timeframe={timeframe}"
            )
        assert self.rate_client is not None
        assert self._download is not None

        # Map first so unknown symbols raise BEFORE we touch the network.
        native = to_native("yahoo", symbol)

        # Throttle then run the sync yfinance call off-loop. yfinance is
        # not an httpx call, so we drain the bucket directly rather than
        # routing through self.rate_client.request().
        await self.rate_client.buckets["default"].acquire(weight=1)

        interval = _TF_TO_YAHOO_INTERVAL[timeframe]
        period = self._period_for_limit(timeframe, limit)

        kwargs: dict[str, Any] = {
            "tickers": native,
            "interval": interval,
            "progress": False,
            "auto_adjust": False,
            "threads": False,
        }
        if start is not None:
            kwargs["start"] = start
            if end is not None:
                kwargs["end"] = end
        else:
            kwargs["period"] = period

        try:
            df = await asyncio.to_thread(self._download, **kwargs)
        except (ConnectionError, TimeoutError, OSError) as e:
            log.warning("yahoo fetch_klines network error: %s", e)
            return []

        if df is None or df.empty:
            return []

        out: list[Candle] = []
        for ts, row in df.iterrows():
            ts_dt = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
            if ts_dt.tzinfo is None:
                ts_dt = ts_dt.replace(tzinfo=timezone.utc)
            out.append(
                Candle(
                    ts=ts_dt,
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    volume=float(row["Volume"]),
                )
            )
        return out

    async def list_symbols(self) -> list[SymbolInfo]:
        # Spec section 3.4: Yahoo has no list-all endpoint. The universe
        # must be seeded manually via tools/data/seed_yahoo_symbols.py.
        return []

    @staticmethod
    def _period_for_limit(timeframe: str, limit: int) -> str:
        """yfinance period strings: 1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, max."""
        if timeframe == "1d":
            if limit <= 30:
                return "1mo"
            if limit <= 180:
                return "6mo"
            if limit <= 365:
                return "1y"
            return "5y"
        if timeframe == "1h":
            return "30d" if limit <= 720 else "60d"
        return "5d"
