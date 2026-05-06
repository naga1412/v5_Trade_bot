"""SP-9 Phase E1: unit tests for L9 news + sentiment aggregator.

Validates the contract documented in spec §3.4:
- abstain (return None) when no news in window
- impact-weighted average sentiment, tanh-squashed
- direction deadband ±0.1
- confidence cap at 1.0 (= n_articles / 5.0)
- lookback filter (default 60 min)
- per-asset filter (BTC query ignores ETH-only articles)

The SQLite test fixture mirrors the SP-9 B5 storage shape
(``affected_assets`` as a JSON-encoded string), so the layer's SQL must
LIKE-match the JSON encoding (``%"BTC"%``) for the SQLite branch.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.scoring.layer9_news import score
from app.core.scoring.types import Direction


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """SQLite in-memory mirror of the news_items table.

    Same schema as the migration's SQLite branch (``affected_assets`` is
    a JSON-encoded TEXT column — see ``app/news/persistence.py``).
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text("""
            CREATE TABLE news_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT,
                url TEXT,
                title TEXT,
                published_at TIMESTAMP NOT NULL,
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


_NOW = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)


async def _insert(
    session: AsyncSession,
    *,
    sentiment: float | None,
    impact: float | None,
    assets_json: str,
    minutes_ago: int,
    url: str = "u",
) -> None:
    await session.execute(
        sa.text(
            "INSERT INTO news_items "
            "(source, url, title, published_at, sentiment_score, "
            " sentiment_confidence, impact_score, affected_assets) "
            "VALUES ('s', :url, 't', :pub, :s, 0.9, :i, :a)"
        ),
        {
            "url": url,
            "pub": _NOW - timedelta(minutes=minutes_ago),
            "s": sentiment,
            "i": impact,
            "a": assets_json,
        },
    )


@pytest.mark.asyncio
async def test_score_returns_none_when_no_news(session: AsyncSession) -> None:
    """Empty news_items table → layer abstains."""
    result = await score(symbol="BTC/USDT", session=session, now=_NOW)
    assert result is None


@pytest.mark.asyncio
async def test_score_three_positive_articles_return_long(
    session: AsyncSession,
) -> None:
    await _insert(session, sentiment=0.8, impact=0.9, assets_json='["BTC"]',
                  minutes_ago=10, url="u1")
    await _insert(session, sentiment=0.6, impact=0.7, assets_json='["BTC"]',
                  minutes_ago=20, url="u2")
    await _insert(session, sentiment=0.5, impact=0.5, assets_json='["BTC"]',
                  minutes_ago=30, url="u3")
    await session.commit()

    result = await score(symbol="BTC/USDT", session=session, now=_NOW)
    assert result is not None
    assert result.direction is Direction.LONG
    assert 0.0 < result.strength <= 1.0
    assert 0.0 < result.confidence <= 1.0


@pytest.mark.asyncio
async def test_score_three_negative_articles_return_short(
    session: AsyncSession,
) -> None:
    await _insert(session, sentiment=-0.8, impact=0.9, assets_json='["BTC"]',
                  minutes_ago=10, url="u1")
    await _insert(session, sentiment=-0.6, impact=0.7, assets_json='["BTC"]',
                  minutes_ago=20, url="u2")
    await _insert(session, sentiment=-0.4, impact=0.5, assets_json='["BTC"]',
                  minutes_ago=30, url="u3")
    await session.commit()

    result = await score(symbol="BTC/USDT", session=session, now=_NOW)
    assert result is not None
    assert result.direction is Direction.SHORT
    assert 0.0 < result.strength <= 1.0


@pytest.mark.asyncio
async def test_score_mixed_articles_return_layer_score(
    session: AsyncSession,
) -> None:
    """1 positive + 1 negative + 1 neutral with similar weights → still
    returns a LayerScore (any direction depending on impact weighting)."""
    await _insert(session, sentiment=0.8, impact=0.9, assets_json='["BTC"]',
                  minutes_ago=10, url="u1")
    await _insert(session, sentiment=-0.6, impact=0.5, assets_json='["BTC"]',
                  minutes_ago=20, url="u2")
    await _insert(session, sentiment=0.0, impact=0.5, assets_json='["BTC"]',
                  minutes_ago=30, url="u3")
    await session.commit()

    result = await score(symbol="BTC/USDT", session=session, now=_NOW)
    assert result is not None
    assert result.direction in {Direction.LONG, Direction.SHORT, Direction.NEUTRAL}
    assert 0.0 <= result.strength <= 1.0


@pytest.mark.asyncio
async def test_score_ignores_articles_outside_lookback_window(
    session: AsyncSession,
) -> None:
    """Article published 2 hours ago is outside the default 60-min window."""
    await _insert(session, sentiment=0.9, impact=1.0, assets_json='["BTC"]',
                  minutes_ago=120, url="u_old")
    await session.commit()

    result = await score(symbol="BTC/USDT", session=session, now=_NOW)
    assert result is None


@pytest.mark.asyncio
async def test_score_ignores_articles_for_other_assets(
    session: AsyncSession,
) -> None:
    """A BTC query should ignore an ETH-only article."""
    await _insert(session, sentiment=0.9, impact=1.0, assets_json='["ETH"]',
                  minutes_ago=10, url="u_eth")
    await session.commit()

    result = await score(symbol="BTC/USDT", session=session, now=_NOW)
    assert result is None


@pytest.mark.asyncio
async def test_score_confidence_caps_at_one(session: AsyncSession) -> None:
    """10 articles → confidence == 1.0 (cap at len/5.0)."""
    for i in range(10):
        await _insert(session, sentiment=0.5, impact=0.5,
                      assets_json='["BTC"]', minutes_ago=5, url=f"u{i}")
    await session.commit()

    result = await score(symbol="BTC/USDT", session=session, now=_NOW)
    assert result is not None
    assert result.confidence == 1.0


@pytest.mark.asyncio
async def test_score_skips_rows_with_null_sentiment(
    session: AsyncSession,
) -> None:
    """Articles with NULL sentiment_score (unclassified) are excluded."""
    await _insert(session, sentiment=None, impact=0.9, assets_json='["BTC"]',
                  minutes_ago=10, url="u_null")
    await session.commit()

    result = await score(symbol="BTC/USDT", session=session, now=_NOW)
    assert result is None


@pytest.mark.asyncio
async def test_score_uses_default_impact_when_null(
    session: AsyncSession,
) -> None:
    """A row with sentiment_score but NULL impact_score defaults impact=0.5."""
    await _insert(session, sentiment=0.9, impact=None, assets_json='["BTC"]',
                  minutes_ago=5, url="u_noimpact")
    await session.commit()

    result = await score(symbol="BTC/USDT", session=session, now=_NOW)
    assert result is not None
    assert result.direction is Direction.LONG


@pytest.mark.asyncio
async def test_score_neutral_direction_when_avg_inside_deadband(
    session: AsyncSession,
) -> None:
    """avg in (-0.1, 0.1) → NEUTRAL direction (still returns a LayerScore)."""
    await _insert(session, sentiment=0.05, impact=0.5, assets_json='["BTC"]',
                  minutes_ago=5, url="u1")
    await _insert(session, sentiment=0.0, impact=0.5, assets_json='["BTC"]',
                  minutes_ago=10, url="u2")
    await session.commit()

    result = await score(symbol="BTC/USDT", session=session, now=_NOW)
    assert result is not None
    assert result.direction is Direction.NEUTRAL


@pytest.mark.asyncio
async def test_score_matches_multi_asset_array(session: AsyncSession) -> None:
    """A BTC-tagged row in a multi-asset article is still picked up."""
    await _insert(session, sentiment=0.7, impact=0.8,
                  assets_json='["BTC", "ETH"]', minutes_ago=5, url="u_multi")
    await session.commit()

    result = await score(symbol="BTC/USDT", session=session, now=_NOW)
    assert result is not None
    assert result.direction is Direction.LONG
