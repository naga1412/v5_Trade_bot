"""PR8 live_cooldowns persistence — UPSERT / LOAD / DELETE.

In-memory SQLite with a CREATE TABLE shim so we test the persistence
helpers without depending on alembic. The introspection test
`tests/db/test_pr8_migration.py` covers the real schema separately.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.live_cooldowns import (
    delete_cooldown,
    load_cooldown,
    upsert_cooldown,
)


_NOW = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)


async def _mk_engine() -> Any:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE live_cooldowns ("
            "user_id INTEGER NOT NULL, "
            "symbol TEXT NOT NULL, "
            "cooldown_until TEXT NOT NULL, "
            "last_exit_reason TEXT NOT NULL, "
            "last_mtf_agreement INTEGER, "
            "updated_at TEXT NOT NULL, "
            "PRIMARY KEY (user_id, symbol))"
        ))
    return engine


@pytest.mark.asyncio
async def test_upsert_insert_then_read_back() -> None:
    engine = await _mk_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await upsert_cooldown(
            s, user_id=1, symbol="BTCUSDT",
            cooldown_until=_NOW + timedelta(hours=8),
            last_exit_reason="stop_loss",
            last_mtf_agreement=4,
        )
        await s.commit()
    async with factory() as s:
        row = await load_cooldown(s, user_id=1, symbol="BTCUSDT")
    assert row is not None
    assert row.user_id == 1
    assert row.symbol == "BTCUSDT"
    assert row.last_exit_reason == "stop_loss"
    assert row.last_mtf_agreement == 4
    assert row.cooldown_until == _NOW + timedelta(hours=8)


@pytest.mark.asyncio
async def test_upsert_updates_existing_row() -> None:
    """Same PK → UPSERT overwrites cooldown_until + last_exit_reason."""
    engine = await _mk_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await upsert_cooldown(
            s, user_id=1, symbol="BTCUSDT",
            cooldown_until=_NOW + timedelta(hours=8),
            last_exit_reason="stop_loss", last_mtf_agreement=3,
        )
        await s.commit()
    async with factory() as s:
        await upsert_cooldown(
            s, user_id=1, symbol="BTCUSDT",
            cooldown_until=_NOW + timedelta(hours=1),  # shorter
            last_exit_reason="take_profit", last_mtf_agreement=5,
        )
        await s.commit()
    async with factory() as s:
        row = await load_cooldown(s, user_id=1, symbol="BTCUSDT")
    assert row.last_exit_reason == "take_profit"
    assert row.last_mtf_agreement == 5
    assert row.cooldown_until == _NOW + timedelta(hours=1)


@pytest.mark.asyncio
async def test_upsert_accepts_null_mtf() -> None:
    """Historic rows may have last_mtf_agreement = None."""
    engine = await _mk_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await upsert_cooldown(
            s, user_id=1, symbol="BTCUSDT",
            cooldown_until=_NOW + timedelta(hours=4),
            last_exit_reason="timeout", last_mtf_agreement=None,
        )
        await s.commit()
    async with factory() as s:
        row = await load_cooldown(s, user_id=1, symbol="BTCUSDT")
    assert row.last_mtf_agreement is None


@pytest.mark.asyncio
async def test_load_cooldown_missing_returns_none() -> None:
    engine = await _mk_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        row = await load_cooldown(s, user_id=1, symbol="ETHUSDT")
    assert row is None


@pytest.mark.asyncio
async def test_load_cooldown_scoped_by_user_id() -> None:
    """Two users with cooldowns on the same symbol must not collide."""
    engine = await _mk_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await upsert_cooldown(
            s, user_id=1, symbol="BTCUSDT",
            cooldown_until=_NOW + timedelta(hours=8),
            last_exit_reason="stop_loss", last_mtf_agreement=4,
        )
        await upsert_cooldown(
            s, user_id=2, symbol="BTCUSDT",
            cooldown_until=_NOW + timedelta(hours=1),
            last_exit_reason="take_profit", last_mtf_agreement=5,
        )
        await s.commit()
    async with factory() as s:
        r1 = await load_cooldown(s, user_id=1, symbol="BTCUSDT")
        r2 = await load_cooldown(s, user_id=2, symbol="BTCUSDT")
    assert r1.last_exit_reason == "stop_loss"
    assert r2.last_exit_reason == "take_profit"


@pytest.mark.asyncio
async def test_delete_cooldown() -> None:
    engine = await _mk_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await upsert_cooldown(
            s, user_id=1, symbol="BTCUSDT",
            cooldown_until=_NOW + timedelta(hours=8),
            last_exit_reason="stop_loss", last_mtf_agreement=4,
        )
        await s.commit()
    async with factory() as s:
        await delete_cooldown(s, user_id=1, symbol="BTCUSDT")
        await s.commit()
    async with factory() as s:
        row = await load_cooldown(s, user_id=1, symbol="BTCUSDT")
    assert row is None


@pytest.mark.asyncio
async def test_delete_missing_row_is_noop() -> None:
    """Deleting a non-existent cooldown must not raise."""
    engine = await _mk_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await delete_cooldown(s, user_id=1, symbol="ETHUSDT")
        await s.commit()  # If delete raised, commit would not reach here.
