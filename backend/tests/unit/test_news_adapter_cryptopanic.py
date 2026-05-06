"""Unit tests for CryptoPanicAdapter (SP-9 Phase B1)."""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest
import respx

from app.news.adapters.cryptopanic import CryptoPanicAdapter


_SAMPLE_RESPONSE = {
    "results": [
        {
            "id": 1,
            "title": "Bitcoin surges past $100k",
            "url": "https://cryptopanic.com/news/1",
            "published_at": "2026-05-06T12:00:00Z",
            "currencies": [{"code": "BTC", "title": "Bitcoin"}],
        },
        {
            "id": 2,
            "title": "SEC delays ETF decision",
            "url": "https://cryptopanic.com/news/2",
            "published_at": "2026-05-06T11:30:00Z",
            "currencies": [],
        },
    ],
    "next": None,
}


@pytest.mark.asyncio
async def test_fetch_recent_returns_articles_published_after_since() -> None:
    async with httpx.AsyncClient() as http:
        with respx.mock(assert_all_called=False) as mock:
            mock.get("https://cryptopanic.com/api/v1/posts/").mock(
                return_value=httpx.Response(200, json=_SAMPLE_RESPONSE)
            )
            adapter = CryptoPanicAdapter(api_key="dummy", http=http)
            since = datetime(2026, 5, 6, 11, 0, tzinfo=timezone.utc)
            articles = await adapter.fetch_recent(since=since)

    assert len(articles) == 2
    assert articles[0].url == "https://cryptopanic.com/news/1"
    assert articles[0].source == "cryptopanic"
    assert articles[0].title.startswith("Bitcoin surges")
    # currencies hint exposed via affected_assets for B3 to merge.
    assert "BTC" in articles[0].affected_assets


@pytest.mark.asyncio
async def test_fetch_recent_skips_articles_at_or_before_since() -> None:
    async with httpx.AsyncClient() as http:
        with respx.mock() as mock:
            mock.get("https://cryptopanic.com/api/v1/posts/").mock(
                return_value=httpx.Response(200, json=_SAMPLE_RESPONSE)
            )
            adapter = CryptoPanicAdapter(api_key="dummy", http=http)
            # `since` is AFTER article 2 (11:30) but BEFORE article 1 (12:00).
            since = datetime(2026, 5, 6, 11, 45, tzinfo=timezone.utc)
            articles = await adapter.fetch_recent(since=since)

    assert len(articles) == 1
    assert articles[0].url.endswith("/1")


@pytest.mark.asyncio
async def test_fetch_recent_returns_empty_on_network_error(caplog) -> None:
    async with httpx.AsyncClient() as http:
        with respx.mock() as mock:
            mock.get("https://cryptopanic.com/api/v1/posts/").mock(
                side_effect=httpx.ConnectError("boom")
            )
            adapter = CryptoPanicAdapter(api_key="dummy", http=http)
            since = datetime(2026, 5, 6, tzinfo=timezone.utc)
            articles = await adapter.fetch_recent(since=since)

    assert articles == []
    assert any("cryptopanic" in r.message.lower() for r in caplog.records)


@pytest.mark.asyncio
async def test_fetch_recent_returns_empty_on_http_error(caplog) -> None:
    async with httpx.AsyncClient() as http:
        with respx.mock() as mock:
            mock.get("https://cryptopanic.com/api/v1/posts/").mock(
                return_value=httpx.Response(500, text="server boom")
            )
            adapter = CryptoPanicAdapter(api_key="dummy", http=http)
            articles = await adapter.fetch_recent(
                since=datetime(2026, 5, 6, tzinfo=timezone.utc),
            )
    assert articles == []
    assert any("cryptopanic" in r.message.lower() for r in caplog.records)


@pytest.mark.asyncio
async def test_fetch_recent_returns_empty_on_invalid_json(caplog) -> None:
    async with httpx.AsyncClient() as http:
        with respx.mock() as mock:
            mock.get("https://cryptopanic.com/api/v1/posts/").mock(
                return_value=httpx.Response(200, text="not json"),
            )
            adapter = CryptoPanicAdapter(api_key="dummy", http=http)
            articles = await adapter.fetch_recent(
                since=datetime(2026, 5, 6, tzinfo=timezone.utc),
            )
    assert articles == []
    assert any("cryptopanic" in r.message.lower() for r in caplog.records)


@pytest.mark.asyncio
async def test_fetch_recent_skips_rows_with_missing_published_at() -> None:
    payload = {
        "results": [
            # Missing published_at — should be skipped silently.
            {"id": 1, "title": "no time", "url": "https://x/1"},
            {
                "id": 2,
                "title": "has time",
                "url": "https://x/2",
                "published_at": "2026-05-06T12:00:00Z",
            },
        ],
        "next": None,
    }
    async with httpx.AsyncClient() as http:
        with respx.mock() as mock:
            mock.get("https://cryptopanic.com/api/v1/posts/").mock(
                return_value=httpx.Response(200, json=payload),
            )
            adapter = CryptoPanicAdapter(api_key="dummy", http=http)
            articles = await adapter.fetch_recent(
                since=datetime(2026, 5, 6, tzinfo=timezone.utc),
            )
    assert len(articles) == 1
    assert articles[0].url.endswith("/2")


@pytest.mark.asyncio
async def test_fetch_recent_handles_empty_results() -> None:
    async with httpx.AsyncClient() as http:
        with respx.mock() as mock:
            mock.get("https://cryptopanic.com/api/v1/posts/").mock(
                return_value=httpx.Response(200, json={"results": [], "next": None}),
            )
            adapter = CryptoPanicAdapter(api_key="dummy", http=http)
            articles = await adapter.fetch_recent(
                since=datetime(2026, 5, 6, tzinfo=timezone.utc),
            )
    assert articles == []


@pytest.mark.asyncio
async def test_fetch_recent_naive_since_treated_as_utc() -> None:
    async with httpx.AsyncClient() as http:
        with respx.mock() as mock:
            mock.get("https://cryptopanic.com/api/v1/posts/").mock(
                return_value=httpx.Response(200, json=_SAMPLE_RESPONSE),
            )
            adapter = CryptoPanicAdapter(api_key="dummy", http=http)
            since_naive = datetime(2026, 5, 6, 11, 0)  # tz-naive
            articles = await adapter.fetch_recent(since=since_naive)
    assert len(articles) == 2


@pytest.mark.asyncio
async def test_fetch_recent_uses_daily_counter_bucket() -> None:
    """Each call should consume one token; 500/day cap exposed via .tokens."""
    async with httpx.AsyncClient() as http:
        with respx.mock() as mock:
            mock.get("https://cryptopanic.com/api/v1/posts/").mock(
                return_value=httpx.Response(200, json={"results": [], "next": None})
            )
            adapter = CryptoPanicAdapter(api_key="dummy", http=http)
            assert adapter.rate_client is not None
            tokens_before = adapter.rate_client.buckets["default"].tokens
            await adapter.fetch_recent(since=datetime(2026, 5, 6, tzinfo=timezone.utc))
            tokens_after = adapter.rate_client.buckets["default"].tokens
    assert tokens_before - tokens_after == 1.0


@pytest.mark.asyncio
async def test_fetch_recent_passes_auth_token_param() -> None:
    async with httpx.AsyncClient() as http:
        with respx.mock() as mock:
            route = mock.get("https://cryptopanic.com/api/v1/posts/").mock(
                return_value=httpx.Response(200, json={"results": [], "next": None}),
            )
            adapter = CryptoPanicAdapter(api_key="my-secret", http=http)
            await adapter.fetch_recent(since=datetime(2026, 5, 6, tzinfo=timezone.utc))
    request_url = str(route.calls[0].request.url)
    assert "auth_token=my-secret" in request_url
    assert "filter=hot" in request_url
    assert "public=true" in request_url


def test_adapter_requires_api_key() -> None:
    with pytest.raises(ValueError, match="api_key"):
        CryptoPanicAdapter(api_key="")
