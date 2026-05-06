"""Admin REST for SP-9 news (Phase F5).

Two endpoints, both gated by :func:`app.auth.deps.require_admin` (mirrors
:mod:`app.api.routes.admin_traps`):

* ``GET /api/v1/admin/news?since=ISO&limit=50&category=...``
  list ``news_items`` rows published after ``since`` (default: last 24h),
  optionally filtered by category, ordered by ``published_at DESC``.
* ``POST /api/v1/admin/news/refresh``
  trigger an immediate fetch+classify+persist round across all configured
  adapters (CryptoPanic + Yahoo RSS by default). Useful for ops + the SP-9
  acceptance test. Returns the number of new rows + the names of the
  adapters that responded.

Cross-dialect storage notes are inherited from :mod:`app.news.persistence`
and :mod:`app.core.scoring.layer9_news`: Postgres stores
``affected_assets`` as ``TEXT[]``, the SQLite test mirror as a JSON-encoded
TEXT column. Both branches are normalised to ``list[str]`` before
serialisation.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import NewsArticleOut, NewsRefreshOut
from app.auth.deps import require_admin
from app.db.session import get_session, get_session_factory


log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/admin/news",
    tags=["admin-news"],
    dependencies=[Depends(require_admin)],
)


def _normalise_assets(raw: Any) -> list[str]:
    """Accept Postgres ``list[str]`` or SQLite JSON-encoded TEXT ``'["BTC"]'``."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw]
    if isinstance(raw, (tuple, set)):
        return [str(x) for x in raw]
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return []
        try:
            decoded = json.loads(s)
            if isinstance(decoded, list):
                return [str(x) for x in decoded]
        except (ValueError, TypeError):
            pass
    return []


@router.get("", response_model=list[NewsArticleOut])
async def list_news(
    since: datetime | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=1000),
    category: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[NewsArticleOut]:
    """List recent ``news_items`` rows.

    ``since`` defaults to "last 24 hours" so admins poking the endpoint
    without query params get a sensible default. ``category`` is an exact
    match on the (nullable) ``news_items.category`` column.
    """
    if since is None:
        since = datetime.now(timezone.utc) - timedelta(hours=24)

    sql = (
        "SELECT id, source, url, title, published_at, fetched_at, "
        "       sentiment_score, sentiment_label, impact_score, "
        "       category, affected_assets "
        "FROM news_items "
        "WHERE published_at >= :since"
    )
    params: dict[str, Any] = {"since": since, "limit": limit}
    if category is not None:
        sql += " AND category = :category"
        params["category"] = category
    sql += " ORDER BY published_at DESC LIMIT :limit"

    rows = (await session.execute(sa.text(sql), params)).all()
    return [
        NewsArticleOut(
            id=int(r.id),
            source=str(r.source),
            url=str(r.url),
            title=str(r.title),
            published_at=r.published_at,
            fetched_at=r.fetched_at,
            sentiment_score=(
                float(r.sentiment_score) if r.sentiment_score is not None else None
            ),
            sentiment_label=r.sentiment_label,
            impact_score=(
                float(r.impact_score) if r.impact_score is not None else None
            ),
            category=r.category,
            affected_assets=_normalise_assets(r.affected_assets),
        )
        for r in rows
    ]


async def _refresh_single_adapter(
    session_factory: Any, adapter: Any, since: datetime,
) -> int:
    """Poll one adapter, classify, persist. Returns # new rows.

    Extracted so :func:`refresh_news` can iterate over the configured
    adapter list and the unit tests can swap in a stub. Runs lazy imports
    to keep the FinBERT / persistence stack out of the cold-start path.
    """
    articles = await adapter.fetch_recent(since=since)
    if not articles:
        return 0

    # Lazy imports so the ~440MB FinBERT torch graph only loads when
    # there's actually something to classify.
    from app.news.persistence import persist_news_items
    from app.news.sentiment import classify_batch

    sentiments = classify_batch([a.title for a in articles])
    async with session_factory() as session:
        return await persist_news_items(session, articles, sentiments)


@router.post("/refresh", response_model=NewsRefreshOut)
async def refresh_news() -> NewsRefreshOut:
    """Trigger an immediate ingest round across all configured adapters.

    Each adapter's failure is logged + swallowed so one broken upstream
    doesn't take the whole refresh down. The response reports the names
    of the adapters that *did* respond + the total rows persisted.
    """
    # Lazy import so the test suite can monkey-patch
    # ``app.api.routes.admin_news._build_adapters`` without dragging in
    # the live HTTP-fetch stack at module import time.
    from app.news.ingest_worker import _build_adapters

    adapters = _build_adapters()
    sf = get_session_factory()
    since = datetime.now(timezone.utc) - timedelta(hours=1)

    total = 0
    polled: list[str] = []
    for adapter in adapters:
        try:
            n = await _refresh_single_adapter(sf, adapter, since)
            total += n
            polled.append(adapter.name)
        except Exception:  # noqa: BLE001
            log.exception(
                "admin_news refresh: adapter %s failed", getattr(adapter, "name", "?"),
            )
    return NewsRefreshOut(new_rows=total, sources_polled=polled)
