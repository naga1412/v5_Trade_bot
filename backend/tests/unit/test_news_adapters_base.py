from datetime import datetime, timezone

import pytest

from app.news.adapters._base import NewsAdapter, NewsArticle


def test_news_article_is_frozen_dataclass() -> None:
    a = NewsArticle(
        source="cryptopanic",
        url="https://example.com/x",
        title="Bitcoin surges",
        body=None,
        published_at=datetime(2026, 5, 6, 12, 0, tzinfo=timezone.utc),
        category="exchange",
        affected_assets=("BTC",),
    )
    assert a.source == "cryptopanic"
    assert a.affected_assets == ("BTC",)
    with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
        a.title = "mutated"  # type: ignore[misc]


def test_news_article_hashable() -> None:
    a = NewsArticle(
        source="x", url="u", title="t", body=None,
        published_at=datetime(2026, 5, 6, tzinfo=timezone.utc),
        category=None, affected_assets=(),
    )
    # Frozen dataclass with all-hashable fields → must be hashable.
    assert hash(a) == hash(a)


def test_news_adapter_is_runtime_checkable_protocol() -> None:
    class FakeAdapter:
        name = "fake"
        async def fetch_recent(self, *, since):  # type: ignore[no-untyped-def]
            return []

    assert isinstance(FakeAdapter(), NewsAdapter)
