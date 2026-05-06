"""Integration tests for ``POST /api/v1/admin/news/refresh`` (SP-9 Phase F5).

Verifies the manual ingest trigger works end-to-end with a stubbed
adapter pair so no network call is required. Mirrors the
``test_api_admin_news_list`` style: admin_client + friend_client +
auth_factory fixtures from ``tests/integration/conftest.py``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.news.adapters._base import NewsArticle


def _stub_adapter(name: str, articles: list[NewsArticle]) -> Any:
    """A MagicMock that exposes ``name`` + an async ``fetch_recent``."""
    m = MagicMock()
    m.name = name
    m.fetch_recent = AsyncMock(return_value=articles)
    return m


def _article(title: str, url: str) -> NewsArticle:
    return NewsArticle(
        source="test",
        url=url,
        title=title,
        body=None,
        published_at=datetime(2026, 5, 6, tzinfo=timezone.utc),
        category=None,
        affected_assets=("BTC",),
    )


@pytest.mark.asyncio
async def test_refresh_returns_zero_when_adapters_empty(  # type: ignore[no-untyped-def]
    admin_client, monkeypatch,
) -> None:
    """Stub adapter returning no articles → 0 new rows + name in polled list."""
    crypto = _stub_adapter("cryptopanic", [])
    yahoo = _stub_adapter("yahoo_rss", [])
    monkeypatch.setattr(
        "app.news.ingest_worker._build_adapters",
        lambda *a, **kw: (crypto, yahoo),
    )
    resp = await admin_client.post("/api/v1/admin/news/refresh")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["new_rows"] == 0
    assert set(body["sources_polled"]) == {"cryptopanic", "yahoo_rss"}


@pytest.mark.asyncio
async def test_refresh_persists_new_articles(  # type: ignore[no-untyped-def]
    admin_client, auth_factory, monkeypatch,
) -> None:
    """Stubbed adapter returns 2 articles → endpoint persists 2 + reports 2."""
    crypto = _stub_adapter("cryptopanic", [
        _article("BTC up 10%", "https://x.test/a1"),
        _article("ETH news", "https://x.test/a2"),
    ])
    yahoo = _stub_adapter("yahoo_rss", [])

    def _stub_classify(_titles: list[str]) -> list:
        from app.news.sentiment import SentimentResult
        return [
            SentimentResult(score=0.3, label="positive", confidence=0.9)
            for _ in _titles
        ]

    monkeypatch.setattr(
        "app.news.ingest_worker._build_adapters",
        lambda *a, **kw: (crypto, yahoo),
    )
    monkeypatch.setattr("app.news.sentiment.classify_batch", _stub_classify)
    # Route the handler's call to ``get_session_factory`` at the production
    # session module to the in-memory auth_factory the admin_client is bound
    # to so persisted rows land in the same DB the test can inspect.
    monkeypatch.setattr(
        "app.api.routes.admin_news.get_session_factory", lambda: auth_factory,
    )

    resp = await admin_client.post("/api/v1/admin/news/refresh")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["new_rows"] == 2
    assert body["sources_polled"] == ["cryptopanic", "yahoo_rss"]


@pytest.mark.asyncio
async def test_refresh_swallows_failing_adapter(  # type: ignore[no-untyped-def]
    admin_client, auth_factory, monkeypatch,
) -> None:
    """A broken adapter is logged + skipped; healthy ones still ship."""
    bad = MagicMock()
    bad.name = "cryptopanic"
    bad.fetch_recent = AsyncMock(side_effect=RuntimeError("boom"))
    good = _stub_adapter("yahoo_rss", [])

    monkeypatch.setattr(
        "app.news.ingest_worker._build_adapters",
        lambda *a, **kw: (bad, good),
    )
    monkeypatch.setattr(
        "app.api.routes.admin_news.get_session_factory", lambda: auth_factory,
    )

    resp = await admin_client.post("/api/v1/admin/news/refresh")
    assert resp.status_code == 200
    body = resp.json()
    # The bad adapter never gets to .name in the polled list because the
    # handler appends after a successful round; the good one does.
    assert "yahoo_rss" in body["sources_polled"]
    assert "cryptopanic" not in body["sources_polled"]
    assert body["new_rows"] == 0


@pytest.mark.asyncio
async def test_refresh_403_for_non_admin(  # type: ignore[no-untyped-def]
    friend_client,
) -> None:
    resp = await friend_client.post("/api/v1/admin/news/refresh")
    assert resp.status_code == 403
