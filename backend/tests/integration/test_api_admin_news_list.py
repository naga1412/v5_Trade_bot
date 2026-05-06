"""Integration tests for ``GET /api/v1/admin/news`` (SP-9 Phase F5).

Mirrors the SP-2 admin-patterns / SP-5 admin-traps suites: seed rows via
the auth_factory's session, then exercise the route through the
admin_client / friend_client fixtures defined in
``tests/integration/conftest.py``.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa


async def _seed_news(
    factory: object,
    *,
    rows: list[dict],
) -> None:
    """INSERT a list of news_items rows into the SQLite mirror.

    Each ``rows`` entry must supply: source, url, title, published_at,
    impact_score, category, affected_assets (list[str]).
    """
    async with factory() as session:  # type: ignore[operator]
        for r in rows:
            await session.execute(
                sa.text(
                    "INSERT INTO news_items "
                    "(source, url, title, published_at, "
                    " sentiment_score, sentiment_label, "
                    " impact_score, category, affected_assets) "
                    "VALUES (:source, :url, :title, :pub, "
                    "        :s_score, :s_label, "
                    "        :impact, :cat, :assets)"
                ),
                {
                    "source": r["source"],
                    "url": r["url"],
                    "title": r["title"],
                    "pub": r["published_at"],
                    "s_score": r.get("sentiment_score"),
                    "s_label": r.get("sentiment_label"),
                    "impact": r.get("impact_score"),
                    "cat": r.get("category"),
                    "assets": json.dumps(r.get("affected_assets", [])),
                },
            )
        await session.commit()


def _row(
    *,
    source: str = "cryptopanic",
    url: str,
    title: str,
    published_at: datetime,
    impact_score: float = 0.5,
    category: str | None = None,
    sentiment_score: float | None = None,
    sentiment_label: str | None = None,
    affected_assets: list[str] | None = None,
) -> dict:
    return {
        "source": source,
        "url": url,
        "title": title,
        "published_at": published_at,
        "impact_score": impact_score,
        "category": category,
        "sentiment_score": sentiment_score,
        "sentiment_label": sentiment_label,
        "affected_assets": affected_assets or ["BTC"],
    }


@pytest.mark.asyncio
async def test_list_news_returns_recent_articles(  # type: ignore[no-untyped-def]
    admin_client, auth_factory,
) -> None:
    now = datetime.now(timezone.utc)
    await _seed_news(auth_factory, rows=[
        _row(url="u1", title="fresh1", published_at=now - timedelta(hours=2)),
        _row(url="u2", title="fresh2", published_at=now - timedelta(hours=3)),
        _row(url="u3", title="stale",  published_at=now - timedelta(days=2)),
    ])
    resp = await admin_client.get("/api/v1/admin/news")
    assert resp.status_code == 200, resp.text
    titles = [r["title"] for r in resp.json()]
    assert "fresh1" in titles and "fresh2" in titles
    assert "stale" not in titles


@pytest.mark.asyncio
async def test_list_news_orders_by_published_at_desc(  # type: ignore[no-untyped-def]
    admin_client, auth_factory,
) -> None:
    """Most-recent article comes first."""
    now = datetime.now(timezone.utc)
    await _seed_news(auth_factory, rows=[
        _row(url="older", title="older", published_at=now - timedelta(hours=10)),
        _row(url="newer", title="newer", published_at=now - timedelta(hours=1)),
    ])
    resp = await admin_client.get("/api/v1/admin/news")
    assert resp.status_code == 200
    rows = resp.json()
    assert rows[0]["title"] == "newer"
    assert rows[1]["title"] == "older"


@pytest.mark.asyncio
async def test_list_news_respects_since_query(  # type: ignore[no-untyped-def]
    admin_client, auth_factory,
) -> None:
    """``?since=ISO`` overrides the 24h default cutoff."""
    now = datetime.now(timezone.utc)
    await _seed_news(auth_factory, rows=[
        _row(url="d4", title="four_days", published_at=now - timedelta(days=4)),
        _row(url="d2", title="two_days",  published_at=now - timedelta(days=2)),
        _row(url="h1", title="one_hour",  published_at=now - timedelta(hours=1)),
    ])
    # Use URL-safe ISO with Z suffix; FastAPI parses both +00:00 and Z but
    # the unencoded "+" in a query string decodes to a space.
    cutoff = (now - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    resp = await admin_client.get(f"/api/v1/admin/news?since={cutoff}")
    assert resp.status_code == 200
    titles = {r["title"] for r in resp.json()}
    assert titles == {"two_days", "one_hour"}


@pytest.mark.asyncio
async def test_list_news_filters_by_category(  # type: ignore[no-untyped-def]
    admin_client, auth_factory,
) -> None:
    now = datetime.now(timezone.utc)
    await _seed_news(auth_factory, rows=[
        _row(url="r1", title="reg_a",   published_at=now - timedelta(hours=1),
             category="regulatory"),
        _row(url="r2", title="reg_b",   published_at=now - timedelta(hours=2),
             category="regulatory"),
        _row(url="e1", title="exch_a",  published_at=now - timedelta(hours=3),
             category="exchange"),
    ])
    resp = await admin_client.get("/api/v1/admin/news?category=regulatory")
    assert resp.status_code == 200
    titles = {r["title"] for r in resp.json()}
    assert titles == {"reg_a", "reg_b"}


@pytest.mark.asyncio
async def test_list_news_limit_clamps_response(  # type: ignore[no-untyped-def]
    admin_client, auth_factory,
) -> None:
    now = datetime.now(timezone.utc)
    await _seed_news(auth_factory, rows=[
        _row(url=f"u{i}", title=f"t{i}",
             published_at=now - timedelta(minutes=i))
        for i in range(10)
    ])
    resp = await admin_client.get("/api/v1/admin/news?limit=3")
    assert resp.status_code == 200
    assert len(resp.json()) == 3


@pytest.mark.asyncio
async def test_list_news_normalises_affected_assets(  # type: ignore[no-untyped-def]
    admin_client, auth_factory,
) -> None:
    """SQLite stores assets as JSON-encoded TEXT — endpoint returns ``list[str]``."""
    now = datetime.now(timezone.utc)
    await _seed_news(auth_factory, rows=[
        _row(url="multi", title="multi", published_at=now - timedelta(hours=1),
             affected_assets=["BTC", "ETH", "SOL"]),
    ])
    resp = await admin_client.get("/api/v1/admin/news")
    body = resp.json()
    assert body[0]["affected_assets"] == ["BTC", "ETH", "SOL"]


@pytest.mark.asyncio
async def test_list_news_403_for_non_admin(friend_client) -> None:  # type: ignore[no-untyped-def]
    resp = await friend_client.get("/api/v1/admin/news")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_list_news_invalid_limit_rejected(  # type: ignore[no-untyped-def]
    admin_client,
) -> None:
    """``limit=0`` violates the FastAPI Query(ge=1) constraint."""
    resp = await admin_client.get("/api/v1/admin/news?limit=0")
    assert resp.status_code == 422
