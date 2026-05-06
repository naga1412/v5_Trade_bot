"""Stub — implemented in SP-9 Phase B5/B6."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.news.adapters._base import NewsArticle


async def persist_news_items(
    session: AsyncSession,
    articles: list[NewsArticle],
    sentiment_results: list,  # list[SentimentResult] - typed in B5
) -> int:
    raise NotImplementedError("SP-9 Phase B5")


async def cleanup_old_news(session: AsyncSession, *, older_than_days: int = 20) -> int:
    raise NotImplementedError("SP-9 Phase B6")
