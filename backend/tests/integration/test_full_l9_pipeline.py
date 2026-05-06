"""SP-9 Phase E3: full L9 pipeline E2E.

Seeds ``news_items`` with a deterministic mix of BTC sentiment, then drives
the layer directly AND through ``build_prediction`` to confirm:

  1. ``layer9_news.score`` reads the SQLite mirror correctly.
  2. ``build_prediction(..., session=session)`` lifts the L9 score into
     the ``layer_scores["9"]`` slot of the ``LivePredictionOut`` payload.
  3. With session=None, L9 still abstains (regression guard).

This bridges the unit tests (which use a hand-rolled news_items table)
and the SP-9 Phase F frontend wire-up (which expects layer_scores["9"]
to be populated by the predictor).
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import pandas as pd
import pytest
import pytest_asyncio
import sqlalchemy as sa
from freezegun import freeze_time
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.predictor import build_prediction
from app.core.scoring.layer9_news import score as layer9_score
from app.core.scoring.types import Direction


_NOW = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)


def _make_bars(n: int = 250) -> pd.DataFrame:
    """Synthetic uptrend bars (>=200 needed for L1 EMA200)."""
    closes = list(np.linspace(100.0, 200.0, n))
    return pd.DataFrame(
        {
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": [1000.0] * n,
        },
        index=pd.date_range("2026-04-25", periods=n, freq="1h", tz="UTC"),
    )


async def _seed_news(
    session: AsyncSession,
    *,
    n_positive: int,
    n_negative: int,
    base: str = "BTC",
    minutes_ago_offset: int = 5,
) -> None:
    """INSERT n_positive + n_negative news_items for ``base`` in the last 30min."""
    rows: list[dict[str, Any]] = []
    for i in range(n_positive):
        rows.append({
            "url": f"https://example.com/p{i}",
            "title": f"{base} bullish #{i}",
            "pub": _NOW - timedelta(minutes=minutes_ago_offset + i),
            "s": 0.7,
            "i": 0.85,
            "a": f'["{base}"]',
        })
    for i in range(n_negative):
        rows.append({
            "url": f"https://example.com/n{i}",
            "title": f"{base} bearish #{i}",
            "pub": _NOW - timedelta(minutes=minutes_ago_offset + n_positive + i),
            "s": -0.6,
            "i": 0.7,
            "a": f'["{base}"]',
        })
    for r in rows:
        await session.execute(
            sa.text(
                "INSERT INTO news_items "
                "(source, url, title, published_at, sentiment_score, "
                " sentiment_label, sentiment_confidence, impact_score, "
                " category, affected_assets) "
                "VALUES ('cryptopanic', :url, :title, :pub, :s, "
                "        'positive', 0.9, :i, 'exchange', :a)"
            ),
            r,
        )
    await session.commit()


@pytest_asyncio.fixture
async def news_session(
    bot_status_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """A session bound to the integration SQLite engine (with news_items)."""
    async with bot_status_factory() as s:
        yield s


@pytest.mark.asyncio
async def test_layer9_score_reads_seeded_news(news_session: AsyncSession) -> None:
    """3 positive + 2 negative BTC articles → L9 LayerScore, direction=LONG."""
    await _seed_news(news_session, n_positive=3, n_negative=2)

    result = await layer9_score(symbol="BTC/USDT", session=news_session, now=_NOW)
    assert result is not None
    # Positive impact-weighted average dominates → LONG.
    assert result.direction is Direction.LONG
    assert 0.0 < result.strength <= 1.0
    # 5 articles → confidence == 5/5 == 1.0.
    assert result.confidence == 1.0


@pytest.mark.asyncio
async def test_build_prediction_populates_layer9_when_session_provided(
    news_session: AsyncSession,
) -> None:
    """E2E: seed news_items → build_prediction(session=) → layer_scores['9'] set."""
    await _seed_news(news_session, n_positive=3, n_negative=2)
    bars = _make_bars()

    # Freeze the L9 clock so the seeded ``published_at`` values are inside
    # the layer's default 60-min lookback window regardless of when the
    # test runs.
    with freeze_time(_NOW):
        pred = await build_prediction(
            symbol="BTC/USDT",
            timeframe="1h",
            bars=bars,
            session=news_session,
        )

    assert pred.layer_scores["9"] is not None
    # Direction should be LONG (3 positives outweigh 2 negatives at similar
    # impact weights).
    assert pred.layer_scores["9"].direction == "LONG"
    assert 0.0 < pred.layer_scores["9"].strength <= 1.0
    assert 0.0 < pred.layer_scores["9"].confidence <= 1.0


@pytest.mark.asyncio
async def test_build_prediction_layer9_none_without_session() -> None:
    """Regression: no session → L9 still abstains (matches pre-SP-9 behavior)."""
    bars = _make_bars()
    pred = await build_prediction(
        symbol="BTC/USDT",
        timeframe="1h",
        bars=bars,
    )
    assert pred.layer_scores["9"] is None


@pytest.mark.asyncio
async def test_build_prediction_layer9_none_with_session_but_no_news(
    news_session: AsyncSession,
) -> None:
    """Empty news_items table + session → L9 still abstains."""
    bars = _make_bars()
    pred = await build_prediction(
        symbol="BTC/USDT",
        timeframe="1h",
        bars=bars,
        session=news_session,
    )
    assert pred.layer_scores["9"] is None


@pytest.mark.asyncio
async def test_build_prediction_layer9_skips_other_assets(
    news_session: AsyncSession,
) -> None:
    """ETH-only news in the table → BTC prediction's L9 abstains."""
    await _seed_news(news_session, n_positive=3, n_negative=0, base="ETH")
    bars = _make_bars()

    with freeze_time(_NOW):
        pred = await build_prediction(
            symbol="BTC/USDT",
            timeframe="1h",
            bars=bars,
            session=news_session,
        )
    assert pred.layer_scores["9"] is None


# --- SP-9 Phase F1: predictor populates `sentiment` + `news` summaries -----


@pytest.mark.asyncio
async def test_build_prediction_populates_news_summary(
    news_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """News summary surfaces top headline (max impact_score) + count + tier."""
    # Stub F&G so the sentiment summary doesn't make a network call. We only
    # assert the news summary in this test, but the predictor will attempt
    # the F&G call — leave it returning None by raising.
    async def _fail() -> Any:
        raise RuntimeError("forced offline")

    monkeypatch.setattr("app.news.fear_greed.get_fear_greed_index", _fail)

    await _seed_news(news_session, n_positive=3, n_negative=2)
    bars = _make_bars()
    with freeze_time(_NOW):
        pred = await build_prediction(
            symbol="BTC/USDT", timeframe="1h", bars=bars, session=news_session,
        )
    assert pred.news is not None
    assert pred.news.recent_count == 5
    # Highest impact_score is 0.85 from the bullish rows; top headline is one
    # of those titles.
    assert pred.news.top_headline is not None
    assert "BTC bullish" in pred.news.top_headline
    # avg(0.85*3 + 0.7*2)/5 = 0.79 → HIGH (>0.7).
    assert pred.news.impact == "HIGH"


@pytest.mark.asyncio
async def test_build_prediction_news_summary_none_when_no_articles(
    news_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fail() -> Any:
        raise RuntimeError("forced offline")

    monkeypatch.setattr("app.news.fear_greed.get_fear_greed_index", _fail)
    bars = _make_bars()
    pred = await build_prediction(
        symbol="BTC/USDT", timeframe="1h", bars=bars, session=news_session,
    )
    assert pred.news is None
    # F&G stubbed to fail → sentiment also None.
    assert pred.sentiment is None


@pytest.mark.asyncio
async def test_build_prediction_populates_sentiment_summary(
    news_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F&G stubbed + L9 LONG → sentiment summary with news_bias=Bullish."""
    from datetime import datetime as _dt

    from app.news.fear_greed import FngResult

    async def _stub() -> FngResult:
        return FngResult(
            value=72,
            label="Greed",
            timestamp=_dt(2026, 5, 6, tzinfo=timezone.utc),
        )

    monkeypatch.setattr("app.news.fear_greed.get_fear_greed_index", _stub)

    await _seed_news(news_session, n_positive=3, n_negative=0)
    bars = _make_bars()
    with freeze_time(_NOW):
        pred = await build_prediction(
            symbol="BTC/USDT", timeframe="1h", bars=bars, session=news_session,
        )
    assert pred.sentiment is not None
    assert pred.sentiment.fng_value == 72
    assert pred.sentiment.fng_label == "Greed"
    assert pred.sentiment.news_bias == "Bullish"


@pytest.mark.asyncio
async def test_build_prediction_summaries_none_without_session() -> None:
    """No session → both summaries default to None."""
    bars = _make_bars()
    pred = await build_prediction(symbol="BTC/USDT", timeframe="1h", bars=bars)
    assert pred.sentiment is None
    assert pred.news is None
