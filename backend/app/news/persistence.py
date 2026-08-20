"""SP-9 Phase B5/B6: news_items INSERT + nightly cleanup."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.news.adapters._base import NewsArticle
from app.news.classify_helpers import (
    classify_category,
    extract_affected_assets,
    impact_score_for,
)
from app.news.sentiment import SentimentResult


log = logging.getLogger(__name__)


def _base_ticker(symbol: str) -> str:
    """"BTCUSDT" -> "BTC"; "1000SHIBUSDT" -> "SHIB". Matches the no-slash,
    USDT-quoted convention every universe table uses."""
    base = symbol[:-4] if symbol.upper().endswith("USDT") else symbol
    if base.upper().startswith("1000"):
        base = base[4:]
    return base.upper()


async def load_universe_tickers(session: AsyncSession) -> frozenset[str]:
    """Union of base tickers from the current live trading universe.

    2026-08-20 (L9 coverage revival, part b): queries both
    ``live_fleet_universe`` (Phase 4's three-cohort selector) and
    ``asset_universe`` (shadow's own top-30) and unions their latest
    snapshots. Two sources, not one, because ``live_fleet_universe``
    does not exist on prod yet (Phase 4 hasn't been cherry-picked from
    dev) -- querying only that table would silently return nothing
    there. Each query is independently best-effort: a missing table or
    any other failure contributes an empty set for that query rather
    than raising, and the session is rolled back afterward so a failed
    SELECT here can never poison a later statement on the same session
    (Postgres aborts the whole transaction after any failed statement
    until rollback) -- this must never be able to break the news INSERT
    that follows it.
    """
    tickers: set[str] = set()
    for query in (
        "SELECT DISTINCT symbol FROM live_fleet_universe "
        "WHERE snapshot_at = (SELECT MAX(snapshot_at) FROM live_fleet_universe)",
        "SELECT DISTINCT symbol FROM asset_universe "
        "WHERE snapshot_at = (SELECT MAX(snapshot_at) FROM asset_universe)",
    ):
        try:
            rows = await session.execute(sa.text(query))
            for r in rows:
                tickers.add(_base_ticker(str(r[0])))
        except Exception as e:  # noqa: BLE001 — best-effort, see docstring
            log.debug("load_universe_tickers: query failed (%s): %s", query, e)
            await session.rollback()
    return frozenset(tickers)


async def persist_news_items(
    session: AsyncSession,
    articles: list[NewsArticle],
    sentiment_results: list[SentimentResult],
    *,
    dynamic_tickers: frozenset[str] = frozenset(),
) -> int:
    """INSERT each article (with sentiment) into ``news_items``. Returns # new rows.

    Pairs ``articles[i]`` with ``sentiment_results[i]`` when present; if
    ``sentiment_results`` is shorter than ``articles``, the trailing articles
    persist with NULL sentiment columns.

    Dedup by URL via ``ON CONFLICT (url) DO NOTHING`` — re-emitted articles
    silently skip. The returned count reflects rows actually inserted.

    Per SP-0.5 hotfix lesson: ``published_at`` is bound as a Python datetime
    (NOT an ISO string) so SQLAlchemy's TIMESTAMP coercion picks the right
    dialect path.

    ``dynamic_tickers`` (2026-08-20): the current universe's ticker set,
    normally from :func:`load_universe_tickers` called by the caller on
    its OWN session (see ``app/news/ingest_worker.py``) — kept separate
    from this function's own session/transaction deliberately, so a
    failed universe query can never abort the INSERT transaction below.
    Defaults to empty so omitting it is bit-identical to pre-2026-08-20
    behavior.
    """
    if not articles:
        return 0

    # Detect dialect once so we can switch ARRAY binding strategies.
    dialect = session.bind.dialect.name if session.bind else "postgresql"
    is_pg = dialect.startswith("postgres")

    inserted = 0
    for i, art in enumerate(articles):
        sent = sentiment_results[i] if i < len(sentiment_results) else None
        # Merge adapter-supplied + title-extracted (curated + dynamic) assets.
        extracted = extract_affected_assets(art.title, dynamic_tickers=dynamic_tickers)
        merged = tuple(sorted(set(art.affected_assets) | set(extracted)))

        category = art.category or classify_category(art.title)
        impact = impact_score_for(category, art.source)

        params: dict[str, object | None] = {
            "source": art.source,
            "url": art.url,
            "title": art.title,
            "body": art.body,
            "published_at": art.published_at,
            "sentiment_score": sent.score if sent is not None else None,
            "sentiment_label": sent.label if sent is not None else None,
            "sentiment_confidence": sent.confidence if sent is not None else None,
            "impact_score": impact,
            "category": category,
        }
        if is_pg:
            params["assets"] = list(merged)
            sql = sa.text("""
                INSERT INTO news_items
                  (source, url, title, body, published_at,
                   sentiment_score, sentiment_label, sentiment_confidence,
                   impact_score, category, affected_assets)
                VALUES
                  (:source, :url, :title, :body, :published_at,
                   :sentiment_score, :sentiment_label, :sentiment_confidence,
                   :impact_score, :category, :assets)
                ON CONFLICT (url) DO NOTHING
            """)
        else:
            # sqlite test fallback — store assets as JSON string.
            params["assets"] = json.dumps(list(merged))
            sql = sa.text("""
                INSERT OR IGNORE INTO news_items
                  (source, url, title, body, published_at,
                   sentiment_score, sentiment_label, sentiment_confidence,
                   impact_score, category, affected_assets)
                VALUES
                  (:source, :url, :title, :body, :published_at,
                   :sentiment_score, :sentiment_label, :sentiment_confidence,
                   :impact_score, :category, :assets)
            """)
        result = await session.execute(sql, params)
        rowcount = getattr(result, "rowcount", 0) or 0
        if rowcount > 0:
            inserted += 1
    await session.commit()
    return inserted


async def cleanup_old_news(
    session: AsyncSession, *, older_than_days: int = 20,
) -> int:
    """DELETE news_items with ``published_at < NOW() - INTERVAL older_than_days``.

    Returns the # deleted. Per MASTER_PLAN §631 the default retention is 20d.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
    result = await session.execute(
        sa.text("DELETE FROM news_items WHERE published_at < :cutoff"),
        {"cutoff": cutoff},
    )
    await session.commit()
    deleted = int(getattr(result, "rowcount", 0) or 0)
    log.info(
        "cleanup_old_news: deleted %d rows older than %dd",
        deleted, older_than_days,
    )
    return deleted
