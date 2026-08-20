"""Yahoo Finance RSS adapter (SP-9 Phase B2).

`feedparser` is sync; we offload to `asyncio.to_thread` so the worker loop
stays async. No rate limit headers, no API key — Yahoo's RSS endpoints
are public. We self-throttle to 1 req/sec via a TokenBucket to be polite.

Default feed set covers BTC, ETH, DXY (USD index), S&P 500 — the macro
tickers that influence crypto — plus 5 crypto-focused outlets (2026-08-20,
L9 coverage revival: Yahoo's macro feed rarely names specific altcoins,
a coverage gap not an access gap). Despite the class/module name, nothing
in `fetch_recent` is Yahoo-specific except `_extract_feed_assets`'s
`?s=` query-param parsing (a no-op for non-Yahoo URLs, which is fine —
symbol matching for every source runs through
`classify_helpers.extract_affected_assets` at persist time regardless,
see `app/news/persistence.py`). Override via `feeds=` for tests / future
expansion.

Each crypto-focused feed URL below was verified live (2026-08-20): all
return real, current items, no signup or API key required.
  - CoinDesk: the bare `/arc/outboundfeeds/rss/` 308-redirects to the
    trailing-slash-free canonical URL used here; feedparser follows
    redirects regardless, canonical form used to skip the round trip.
  - Bitcoin Magazine: `.rss/full/` 301-redirects to `bitcoinmagazine.com
    /feed`, which serves over PLAIN HTTP, not HTTPS (site-side issue, not
    ours). Accepted as-is: this is a public news feed (no credentials,
    no user data), and HTTPS on the other 4 feeds does not itself
    authenticate content — none of these outlets cryptographically sign
    headlines, so the marginal risk is one additional MITM-injectable
    hop for one of five low-stakes sources. Revisit if this feed is ever
    used for anything beyond FinBERT sentiment/classification input.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

import feedparser

from app.data.ratelimit import TokenBucket
from app.news.adapters._base import NewsArticle


log = logging.getLogger(__name__)


_DEFAULT_FEEDS: tuple[str, ...] = (
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=BTC-USD&region=US&lang=en-US",
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=ETH-USD&region=US&lang=en-US",
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=^DXY&region=US&lang=en-US",
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=^GSPC&region=US&lang=en-US",
    # Crypto-focused outlets (2026-08-20) -- see module docstring.
    "https://www.coindesk.com/arc/outboundfeeds/rss",
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
    "https://www.theblock.co/rss.xml",
    "http://bitcoinmagazine.com/feed",
)


def _extract_feed_assets(feed_url: str) -> tuple[str, ...]:
    """Pull the `s=BTC-USD` token off a Yahoo RSS URL → ('BTC',)."""
    qs = parse_qs(urlparse(feed_url).query)
    s = qs.get("s", [""])[0]
    if not s:
        return ()
    # "BTC-USD" → "BTC"; "^DXY" → "DXY"; "AAPL" → "AAPL".
    cleaned = re.sub(r"[\^]", "", s).split("-")[0].upper()
    return (cleaned,) if cleaned else ()


@dataclass
class YahooRssAdapter:
    """SP-9 NewsAdapter implementation for Yahoo Finance RSS feeds."""

    feeds: tuple[str, ...] = _DEFAULT_FEEDS
    rate_bucket: TokenBucket | None = None
    name: str = field(default="yahoo_rss", init=False)

    def __post_init__(self) -> None:
        if self.rate_bucket is None:
            self.rate_bucket = TokenBucket(capacity=1, refill_per_sec=1.0)

    async def fetch_recent(self, *, since: datetime) -> list[NewsArticle]:
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        all_articles: list[NewsArticle] = []
        for feed_url in self.feeds:
            assert self.rate_bucket is not None
            await self.rate_bucket.acquire(1)
            try:
                parsed: Any = await asyncio.to_thread(feedparser.parse, feed_url)
            except (OSError, ValueError) as e:
                log.warning("yahoo_rss feed=%s error: %s", feed_url, e)
                continue
            if getattr(parsed, "bozo", 0) and not getattr(parsed, "entries", None):
                log.warning("yahoo_rss feed=%s malformed", feed_url)
                continue
            assets = _extract_feed_assets(feed_url)
            for entry in getattr(parsed, "entries", []):
                published_at = self._extract_dt(entry)
                if published_at is None or published_at <= since:
                    continue
                title = str(getattr(entry, "title", "")).strip()
                link = str(getattr(entry, "link", "")).strip()
                if not link or not title:
                    continue
                summary = str(getattr(entry, "summary", "") or "")[:2000] or None
                all_articles.append(
                    NewsArticle(
                        source=self.name,
                        url=link,
                        title=title,
                        body=summary,
                        published_at=published_at,
                        category=None,
                        affected_assets=assets,
                    )
                )
        return all_articles

    @staticmethod
    def _extract_dt(entry: Any) -> datetime | None:
        pp = getattr(entry, "published_parsed", None)
        if pp is None:
            return None
        try:
            return datetime(
                pp[0], pp[1], pp[2], pp[3], pp[4], pp[5], tzinfo=timezone.utc,
            )
        except (TypeError, ValueError):
            return None
