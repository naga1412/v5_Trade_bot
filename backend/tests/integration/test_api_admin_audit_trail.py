"""Integration tests for GET /api/v1/admin/audit-trail (Phase G8)."""

from __future__ import annotations

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.auth.models import AuthViolation, ImpersonationEvent


async def _seed_audit_rows(factory: async_sessionmaker) -> None:
    """Seed mixed rows across hash-chained + audit tables."""
    async with factory() as s:
        # Create the hash-chained tables (sqlite parity, minimal columns).
        await s.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS predictions ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, "
            "symbol TEXT NOT NULL, timeframe TEXT NOT NULL, "
            "ts TEXT NOT NULL DEFAULT (datetime('now')), direction TEXT, "
            "final_score REAL, prev_hash TEXT, row_hash TEXT)"
        ))
        await s.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS paper_trades ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, "
            "symbol TEXT NOT NULL, direction TEXT NOT NULL, "
            "opened_at TEXT NOT NULL DEFAULT (datetime('now')), "
            "entry_price REAL, prev_hash TEXT, row_hash TEXT)"
        ))
        await s.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS shadow_trades ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, "
            "symbol TEXT NOT NULL, timeframe TEXT NOT NULL, "
            "direction TEXT NOT NULL, "
            "entry_price REAL NOT NULL DEFAULT 0, "
            "stop_loss REAL NOT NULL DEFAULT 0, "
            "take_profit REAL NOT NULL DEFAULT 0, "
            "position_size_usdt REAL NOT NULL DEFAULT 0, "
            "entry_score REAL NOT NULL DEFAULT 0, "
            "entry_confidence REAL NOT NULL DEFAULT 0, "
            "layer_scores TEXT NOT NULL DEFAULT '{}', "
            "entry_atr REAL NOT NULL DEFAULT 0, "
            "opened_at TEXT NOT NULL DEFAULT (datetime('now')), "
            "inputs_hash TEXT NOT NULL DEFAULT '', "
            "model_version TEXT NOT NULL DEFAULT 'sp-0', "
            "signal_id TEXT NOT NULL UNIQUE, "
            "prev_hash TEXT NOT NULL DEFAULT '', "
            "row_hash TEXT NOT NULL UNIQUE)"
        ))
        await s.commit()

        # Predictions: user=1 + user=2
        await s.execute(sa.text(
            "INSERT INTO predictions (user_id, symbol, timeframe, ts, "
            "direction, final_score, prev_hash, row_hash) VALUES "
            "(1, 'BTC/USDT', '1h', '2026-04-01T00:00:00+00:00', "
            " 'LONG', 0.7, '0', 'h1'), "
            "(2, 'ETH/USDT', '1h', '2026-04-02T00:00:00+00:00', "
            " 'SHORT', -0.4, 'h1', 'h2')"
        ))
        # Paper trades: user=1
        await s.execute(sa.text(
            "INSERT INTO paper_trades (user_id, symbol, direction, opened_at, "
            "entry_price, prev_hash, row_hash) VALUES "
            "(1, 'BTC/USDT', 'LONG', '2026-04-03T00:00:00+00:00', "
            " 50000, '0', 'h3')"
        ))
        # Shadow trades: user=2
        await s.execute(sa.text(
            "INSERT INTO shadow_trades (user_id, symbol, timeframe, direction, "
            "opened_at, signal_id, row_hash) VALUES "
            "(2, 'ETH/USDT', '1h', 'SHORT', "
            " '2026-04-04T00:00:00+00:00', 'sig-1', 'h4')"
        ))
        await s.commit()

        s.add(AuthViolation(
            attempted_email="bad@x.com", reason="not_invited",
        ))
        s.add(ImpersonationEvent(
            admin_user_id=1, target_user_id=2, action="start",
        ))
        await s.commit()


@pytest.mark.asyncio
async def test_audit_trail_returns_unified_sorted_desc(
    admin_client: httpx.AsyncClient,
    auth_factory: async_sessionmaker,
) -> None:
    await _seed_audit_rows(auth_factory)
    r = await admin_client.get("/api/v1/admin/audit-trail")
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body, list)
    assert len(body) >= 5
    tables = {row["table_name"] for row in body}
    assert "predictions" in tables
    assert "paper_trades" in tables
    assert "shadow_trades" in tables
    assert "auth_violations" in tables
    assert "impersonation_events" in tables
    # Sorted by ts DESC.
    timestamps = [row["ts"] for row in body]
    assert timestamps == sorted(timestamps, reverse=True)


@pytest.mark.asyncio
async def test_audit_trail_filter_by_user(
    admin_client: httpx.AsyncClient,
    auth_factory: async_sessionmaker,
) -> None:
    await _seed_audit_rows(auth_factory)
    r = await admin_client.get("/api/v1/admin/audit-trail?user_id=1")
    assert r.status_code == 200
    body = r.json()
    # Per-user tables filtered, system tables (auth_violations,
    # impersonation_events) are excluded when user_id filter is set.
    tables = {row["table_name"] for row in body}
    assert "auth_violations" not in tables
    assert "impersonation_events" not in tables
    for row in body:
        assert row.get("user_id") == 1


@pytest.mark.asyncio
async def test_audit_trail_filter_by_table(
    admin_client: httpx.AsyncClient,
    auth_factory: async_sessionmaker,
) -> None:
    await _seed_audit_rows(auth_factory)
    r = await admin_client.get("/api/v1/admin/audit-trail?table=predictions")
    assert r.status_code == 200
    body = r.json()
    assert all(row["table_name"] == "predictions" for row in body)
    assert len(body) == 2


@pytest.mark.asyncio
async def test_audit_trail_pagination(
    admin_client: httpx.AsyncClient,
    auth_factory: async_sessionmaker,
) -> None:
    await _seed_audit_rows(auth_factory)
    r = await admin_client.get("/api/v1/admin/audit-trail?limit=2&offset=0")
    assert r.status_code == 200
    assert len(r.json()) == 2


@pytest.mark.asyncio
async def test_audit_trail_403_for_non_admin(
    friend_client: httpx.AsyncClient,
) -> None:
    r = await friend_client.get("/api/v1/admin/audit-trail")
    assert r.status_code == 403
