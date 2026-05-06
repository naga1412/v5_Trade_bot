"""Unit tests for SP-9 Phase B5/B6 news_items persistence."""
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.news.adapters._base import NewsArticle
from app.news.persistence import cleanup_old_news, persist_news_items
from app.news.sentiment import SentimentResult


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """SQLite in-memory mirror of the news_items table.

    The Postgres production schema uses TEXT[] + GIN for affected_assets;
    SQLite stores the comma-separated/JSON encoding via the persistence
    helper's dialect split (B5).
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text("""
            CREATE TABLE news_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                url TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                body TEXT,
                published_at TIMESTAMP NOT NULL,
                fetched_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                sentiment_score REAL,
                sentiment_label TEXT,
                sentiment_confidence REAL,
                impact_score REAL,
                category TEXT,
                affected_assets TEXT
            )
        """))
    async with AsyncSession(engine) as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_persist_news_items_inserts_rows(session: AsyncSession) -> None:
    articles = [
        NewsArticle(
            source="cryptopanic", url="https://example.com/1",
            title="Bitcoin surges past $100k", body=None,
            published_at=datetime(2026, 5, 6, 12, tzinfo=timezone.utc),
            category=None, affected_assets=("BTC",),
        ),
    ]
    sents = [SentimentResult(score=0.8, label="positive", confidence=0.95)]
    n = await persist_news_items(session, articles, sents)
    assert n == 1
    row = (await session.execute(sa.text("SELECT * FROM news_items"))).first()
    assert row is not None
    assert row.title.startswith("Bitcoin surges")
    assert row.sentiment_score == 0.8
    assert row.sentiment_label == "positive"
    # 'Bitcoin surges past $100k' has no category keyword match → None.
    assert row.category is None
    assert row.impact_score is not None
    assert 0.0 <= row.impact_score <= 1.0


@pytest.mark.asyncio
async def test_persist_news_items_dedupes_by_url(session: AsyncSession) -> None:
    article = NewsArticle(
        source="cryptopanic", url="https://example.com/dup",
        title="X", body=None,
        published_at=datetime(2026, 5, 6, tzinfo=timezone.utc),
        category=None, affected_assets=(),
    )
    sent = SentimentResult(score=0.0, label="neutral", confidence=0.5)
    assert await persist_news_items(session, [article], [sent]) == 1
    # Second insert: same URL → 0 new rows.
    assert await persist_news_items(session, [article], [sent]) == 0
    cnt = (await session.execute(sa.text("SELECT COUNT(*) FROM news_items"))).scalar()
    assert cnt == 1


@pytest.mark.asyncio
async def test_persist_news_items_handles_missing_sentiment(
    session: AsyncSession,
) -> None:
    article = NewsArticle(
        source="yahoo_rss", url="https://example.com/y",
        title="SEC announces crackdown", body=None,
        published_at=datetime(2026, 5, 6, tzinfo=timezone.utc),
        category=None, affected_assets=(),
    )
    n = await persist_news_items(session, [article], sentiment_results=[])
    assert n == 1
    row = (await session.execute(sa.text("SELECT * FROM news_items"))).first()
    assert row is not None
    assert row.sentiment_score is None
    assert row.sentiment_label is None
    assert row.sentiment_confidence is None
    # 'SEC' keyword match → regulatory.
    assert row.category == "regulatory"


@pytest.mark.asyncio
async def test_persist_news_items_returns_zero_for_empty_articles(
    session: AsyncSession,
) -> None:
    assert await persist_news_items(session, [], []) == 0


@pytest.mark.asyncio
async def test_persist_news_items_merges_assets(session: AsyncSession) -> None:
    """Adapter-supplied assets merge with title-extracted ones; sorted+unique."""
    article = NewsArticle(
        source="cryptopanic", url="https://example.com/m",
        title="Bitcoin and Ethereum surge",  # extracts BTC, ETH
        body=None,
        published_at=datetime(2026, 5, 6, tzinfo=timezone.utc),
        category=None,
        affected_assets=("SOL",),  # adapter hint adds SOL
    )
    sent = SentimentResult(score=0.5, label="positive", confidence=0.8)
    assert await persist_news_items(session, [article], [sent]) == 1
    row = (await session.execute(
        sa.text("SELECT affected_assets FROM news_items"),
    )).first()
    assert row is not None
    # SQLite path stores as JSON string of sorted unique list.
    assert "BTC" in row.affected_assets
    assert "ETH" in row.affected_assets
    assert "SOL" in row.affected_assets


@pytest.mark.asyncio
async def test_persist_news_items_uses_supplied_category_over_classifier(
    session: AsyncSession,
) -> None:
    """When the article already has a category, classifier is NOT re-run."""
    article = NewsArticle(
        source="cryptopanic",
        url="https://example.com/cat",
        # title has 'sec' which would normally → regulatory
        title="SEC files paper",
        body=None,
        published_at=datetime(2026, 5, 6, tzinfo=timezone.utc),
        category="exchange",  # but adapter already classified
        affected_assets=(),
    )
    sent = SentimentResult(score=0.0, label="neutral", confidence=0.5)
    await persist_news_items(session, [article], [sent])
    row = (await session.execute(sa.text("SELECT category FROM news_items"))).first()
    assert row is not None
    assert row.category == "exchange"


@pytest.mark.asyncio
async def test_persist_news_items_pairs_each_article_with_its_sentiment(
    session: AsyncSession,
) -> None:
    """Mixed pairing: article[0]+sent[0] but article[1] has no sentiment."""
    arts = [
        NewsArticle(
            source="cryptopanic", url="https://example.com/a1",
            title="t1", body=None,
            published_at=datetime(2026, 5, 6, tzinfo=timezone.utc),
            category=None, affected_assets=(),
        ),
        NewsArticle(
            source="cryptopanic", url="https://example.com/a2",
            title="t2", body=None,
            published_at=datetime(2026, 5, 6, tzinfo=timezone.utc),
            category=None, affected_assets=(),
        ),
    ]
    sents = [SentimentResult(score=0.7, label="positive", confidence=0.9)]
    assert await persist_news_items(session, arts, sents) == 2
    rows = (await session.execute(
        sa.text("SELECT url, sentiment_score FROM news_items ORDER BY url"),
    )).all()
    assert rows[0].sentiment_score == 0.7
    assert rows[1].sentiment_score is None


# -- B6 cleanup tests ---------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_old_news_deletes_rows_older_than_cutoff(
    session: AsyncSession,
) -> None:
    from freezegun import freeze_time
    # Insert one stale (25d) and one fresh (5d) article.
    await session.execute(sa.text("""
        INSERT INTO news_items (source, url, title, published_at, impact_score)
        VALUES ('x','u_old','old','2026-04-10T00:00:00Z',0.5),
               ('x','u_new','new','2026-05-01T00:00:00Z',0.5)
    """))
    await session.commit()
    with freeze_time("2026-05-06T12:00:00Z"):
        n = await cleanup_old_news(session, older_than_days=20)
    assert n == 1
    rows = (await session.execute(sa.text("SELECT url FROM news_items"))).all()
    assert {r.url for r in rows} == {"u_new"}


@pytest.mark.asyncio
async def test_cleanup_old_news_returns_zero_when_nothing_to_delete(
    session: AsyncSession,
) -> None:
    from freezegun import freeze_time
    await session.execute(sa.text("""
        INSERT INTO news_items (source, url, title, published_at, impact_score)
        VALUES ('x','u1','a','2026-05-01T00:00:00Z',0.5)
    """))
    await session.commit()
    with freeze_time("2026-05-06T12:00:00Z"):
        n = await cleanup_old_news(session, older_than_days=20)
    assert n == 0

