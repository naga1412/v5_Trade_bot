"""CryptoPanic free-tier news adapter (SP-9 Phase B1).

Free tier rate limit: 500 calls/day. We use SP-3's `DailyCounterBucket`
(same primitive TwelveData uses) wrapped in a `RateLimitedClient` so the
counter resets at 00:00 UTC. Each `fetch_recent()` call costs 1 token.

Returns articles sorted newest-first per the CryptoPanic response shape;
filters out anything whose `published_at` is at-or-before the caller's
`since` cursor.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from app.data.ratelimit import DailyCounterBucket, RateLimitedClient
from app.news.adapters._base import NewsArticle


log = logging.getLogger(__name__)


_BASE_URL = "https://cryptopanic.com/api/v1/posts/"
_DAILY_LIMIT = 500


def _default_rate_client(http: httpx.AsyncClient) -> RateLimitedClient:
    return RateLimitedClient(
        exchange="cryptopanic",
        http=http,
        buckets={"default": DailyCounterBucket(daily_limit=_DAILY_LIMIT)},
    )


@dataclass
class CryptoPanicAdapter:
    """SP-9 NewsAdapter implementation for CryptoPanic free tier."""

    api_key: str
    http: httpx.AsyncClient | None = None
    rate_client: RateLimitedClient | None = None
    name: str = field(default="cryptopanic", init=False)

    def __post_init__(self) -> None:
        if not self.api_key:
            raise ValueError("CryptoPanicAdapter requires non-empty api_key")
        if self.http is None:
            self.http = httpx.AsyncClient()
        if self.rate_client is None:
            self.rate_client = _default_rate_client(self.http)

    async def fetch_recent(self, *, since: datetime) -> list[NewsArticle]:
        assert self.rate_client is not None
        params: dict[str, Any] = {
            "auth_token": self.api_key,
            "filter": "hot",
            "public": "true",
        }
        try:
            resp = await self.rate_client.request(
                "GET", _BASE_URL, params=params, timeout=10.0,
            )
            resp.raise_for_status()
        except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPError) as e:
            log.warning("cryptopanic fetch_recent error: %s", e)
            return []

        try:
            payload = resp.json()
        except ValueError:
            log.warning("cryptopanic returned non-JSON body")
            return []

        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)

        articles: list[NewsArticle] = []
        for row in payload.get("results", []):
            try:
                published_at = datetime.fromisoformat(
                    str(row["published_at"]).replace("Z", "+00:00")
                )
            except (KeyError, ValueError):
                continue
            if published_at <= since:
                continue
            currencies = tuple(
                (c.get("code") or "").upper()
                for c in (row.get("currencies") or [])
                if c.get("code")
            )
            articles.append(
                NewsArticle(
                    source=self.name,
                    url=str(row.get("url", "")),
                    title=str(row.get("title", "")),
                    body=None,  # free tier doesn't expose body.
                    published_at=published_at,
                    category=None,
                    affected_assets=currencies,
                )
            )
        return articles
