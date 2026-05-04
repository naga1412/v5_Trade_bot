"""Tests for the per-user table query guard (spec §7.3).

The guard listens on `before_cursor_execute` and inspects rendered SQL.
- In dev mode (ENV=development), a SELECT/UPDATE/DELETE against a per-user
  table that omits a `user_id` predicate raises MissingUserIdFilterError.
- In prod mode (ENV != development), the same statement logs a WARNING but
  is allowed to execute so a single missed predicate does not break a live
  request flow.
- Statements against shared tables (e.g. asset_universe) are unaffected.
"""

from __future__ import annotations

import logging

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.auth.query_guard import (
    PER_USER_TABLES,
    MissingUserIdFilterError,
    attach_query_guard,
)


@pytest.mark.asyncio
async def test_select_without_user_id_raises_in_dev() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE shadow_trades (id INTEGER PRIMARY KEY, user_id INTEGER, "
            "symbol TEXT, pnl_pct REAL)"
        ))

    attach_query_guard(engine.sync_engine, dev_mode=True)

    async with AsyncSession(engine) as session:
        with pytest.raises(MissingUserIdFilterError):
            await session.execute(sa.text("SELECT * FROM shadow_trades"))


@pytest.mark.asyncio
async def test_select_with_user_id_passes() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE shadow_trades (id INTEGER PRIMARY KEY, user_id INTEGER, "
            "symbol TEXT, pnl_pct REAL)"
        ))

    attach_query_guard(engine.sync_engine, dev_mode=True)

    async with AsyncSession(engine) as session:
        # Should not raise.
        await session.execute(
            sa.text("SELECT * FROM shadow_trades WHERE user_id = :uid"),
            {"uid": 1},
        )


@pytest.mark.asyncio
async def test_update_without_user_id_raises_in_dev() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE shadow_open_positions ("
            "id INTEGER PRIMARY KEY, user_id INTEGER, "
            "symbol TEXT, bars_held INTEGER)"
        ))

    attach_query_guard(engine.sync_engine, dev_mode=True)

    async with AsyncSession(engine) as session:
        with pytest.raises(MissingUserIdFilterError):
            await session.execute(
                sa.text("UPDATE shadow_open_positions SET bars_held = 1")
            )


@pytest.mark.asyncio
async def test_delete_without_user_id_raises_in_dev() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE shadow_cooldowns (user_id INTEGER, symbol TEXT, "
            "cooldown_until TEXT)"
        ))

    attach_query_guard(engine.sync_engine, dev_mode=True)

    async with AsyncSession(engine) as session:
        with pytest.raises(MissingUserIdFilterError):
            await session.execute(sa.text(
                "DELETE FROM shadow_cooldowns WHERE symbol = 'BTCUSDT'"
            ))


@pytest.mark.asyncio
async def test_select_against_shared_table_passes() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE asset_universe (id INTEGER PRIMARY KEY, symbol TEXT)"
        ))

    attach_query_guard(engine.sync_engine, dev_mode=True)

    async with AsyncSession(engine) as session:
        await session.execute(sa.text("SELECT * FROM asset_universe"))


@pytest.mark.asyncio
async def test_insert_does_not_trigger_guard() -> None:
    """The guard targets reads/mutations against existing rows; INSERTs are fine."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE predictions (id INTEGER PRIMARY KEY, user_id INTEGER, "
            "symbol TEXT)"
        ))

    attach_query_guard(engine.sync_engine, dev_mode=True)

    async with AsyncSession(engine) as session:
        # No user_id in the WHERE; INSERT is exempt.
        await session.execute(
            sa.text("INSERT INTO predictions (user_id, symbol) VALUES (1, 'BTC')")
        )


@pytest.mark.asyncio
async def test_warns_only_in_production_mode(
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE predictions (id INTEGER PRIMARY KEY, user_id INTEGER)"
        ))

    attach_query_guard(engine.sync_engine, dev_mode=False)

    with caplog.at_level(logging.WARNING):
        async with AsyncSession(engine) as session:
            # Should NOT raise; should warn.
            await session.execute(sa.text("SELECT * FROM predictions"))
    assert any("user_id" in r.message for r in caplog.records)


def test_per_user_tables_set_matches_spec() -> None:
    """Spec §7.1 — exactly these five tables are user-scoped."""
    assert PER_USER_TABLES == frozenset({
        "predictions",
        "paper_trades",
        "shadow_trades",
        "shadow_open_positions",
        "shadow_cooldowns",
    })
