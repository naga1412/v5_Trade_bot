"""Binance Futures intermarket adapter (SP-3.5).

Stub — :func:`fetch_snapshot` is implemented in Phase B1. The
:class:`IntermarketSnapshot` dataclass is final and reused by the
persistence layer + predictor helper.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class IntermarketSnapshot:
    """One funding-rate + OI sample for a single symbol at a single instant.

    Returned by :class:`BinanceFuturesIntermarketAdapter.fetch_snapshot` and
    by the persistence-layer reader. ``funding_rate``/``mark_price``/
    ``open_interest`` may be None when the upstream call partially failed
    (e.g., funding endpoint OK but OI endpoint timed out).
    """

    symbol: str
    captured_at: datetime
    funding_rate: float | None
    mark_price: float | None
    open_interest: float | None
    source: str  # "binance_futures" | "bybit"


@dataclass
class BinanceFuturesIntermarketAdapter:
    name: str = "binance_futures"

    async def fetch_snapshot(self, symbol: str) -> IntermarketSnapshot | None:
        raise NotImplementedError("SP-3.5 Phase B1")
