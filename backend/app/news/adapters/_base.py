"""SP-9 NewsAdapter Protocol + NewsArticle dataclass.

Mirrors :mod:`app.data.adapters._base` (SP-3 ExchangeAdapter shape) but for
news sources rather than OHLCV exchanges.

A `NewsAdapter` only needs to expose `name` and `fetch_recent(*, since)`.
Per-source extensions (the CryptoPanic free-tier filter, for example) live
on the concrete classes — call sites that need them must accept the concrete
type, not the Protocol.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class NewsArticle:
    """A single news headline (and optional body) plus metadata.

    Hashable + immutable so call sites can pass these through ``dataclasses.replace``
    to enrich (e.g., attach a sentiment_score) without surprising mutations.

    `affected_assets` is a tuple (not list) for hashability. Convention is
    UPPER-case base symbol only — ``("BTC", "ETH")`` not ``("BTC/USDT",)``.
    """

    source: str
    url: str
    title: str
    body: str | None
    published_at: datetime
    category: str | None
    affected_assets: tuple[str, ...]


@runtime_checkable
class NewsAdapter(Protocol):
    """Minimum surface every news adapter must expose."""

    name: str  # 'cryptopanic' | 'yahoo_rss'

    async def fetch_recent(self, *, since: datetime) -> list[NewsArticle]:
        """Return articles published strictly after `since` (UTC, tz-aware).

        Network/timeout errors return an empty list with a log warning.
        Malformed JSON / XML raises so the caller sees the failure.
        """
        ...
