"""Shared types for app.data.adapters.* (SP-3 spec §3.2).

The Protocol is intentionally narrow: only the two methods every adapter
MUST implement. Per-exchange extensions (websocket streams, contract metadata,
funding rate fetchers) live on the adapter classes themselves and are NOT
part of the Protocol — call sites that need them must accept a concrete
adapter type, not the Protocol.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol, runtime_checkable


AssetClass = Literal["crypto", "stock", "fx", "commodity", "index"]


@dataclass(frozen=True)
class Candle:
    """A single OHLCV bar straight from an exchange.

    `ts` is the bar OPEN time, in UTC (always tz-aware). The adapter is
    responsible for converting exchange-specific timestamps (ms epoch,
    seconds, ISO) into a tz-aware datetime.
    """
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class SymbolInfo:
    """Metadata for a single tradable instrument on a given exchange."""
    canonical: str            # "BTC/USDT" — internal canonical form
    native: str               # exchange-specific: "BTCUSDT" / "BTC-USD"
    base: str                 # "BTC"
    quote: str                # "USDT" — empty string for stocks/indices
    listed_at: datetime | None
    delisted_at: datetime | None
    asset_class: AssetClass


@runtime_checkable
class ExchangeAdapter(Protocol):
    """The minimum surface every data adapter must expose."""

    name: str  # "binance", "bybit", "yahoo", "twelvedata"

    async def fetch_klines(
        self, *,
        symbol: str,
        timeframe: str,
        limit: int = 500,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Candle]:
        """Fetch up to `limit` bars for `symbol` at `timeframe`.

        - `symbol` is the **canonical** form (`BTC/USDT`); the adapter
          converts via its own `to_native()`.
        - `start` / `end` may be None — semantics are exchange-specific
          (e.g., Binance: most-recent N bars).
        - Network/timeout errors return an empty list with a log warning.
        - Malformed JSON raises (the caller sees the failure).
        """
        ...

    async def list_symbols(self) -> list[SymbolInfo]:
        """Return all symbols currently tradable on this exchange.

        - Exchanges without a list-all endpoint (Yahoo, TwelveData free)
          return an empty list — the universe must be seeded manually for
          those adapters via tools/data/seed_*_symbols.py.
        """
        ...
