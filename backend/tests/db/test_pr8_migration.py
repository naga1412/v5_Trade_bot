"""Migration tests for 0022_pr8_live_cooldowns.

Verifies the schema introduced by the PR8 migration:
  - live_cooldowns table exists with the spec'd columns.
  - PRIMARY KEY is (user_id, symbol).
  - Partial active-only index exists (Postgres only).
  - cooldown_until + updated_at are TIMESTAMPTZ (NOT NULL).
  - last_mtf_agreement is nullable (historic trades lack the column).

Postgres-only. SQLite tests skip via the `postgres_db` guard.
"""
from __future__ import annotations

import os

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine


_DSN = os.environ.get("DATABASE_URL", "")
_IS_PG = _DSN.startswith("postgresql")


pytestmark = pytest.mark.skipif(
    not _IS_PG,
    reason="Postgres DATABASE_URL not set — migration tests are CI-only. "
    "Set DATABASE_URL=postgresql+asyncpg://... to run locally.",
)


@pytest.mark.asyncio
async def test_live_cooldowns_table_exists() -> None:
    engine = create_async_engine(_DSN)
    async with engine.connect() as conn:
        rows = (await conn.execute(sa.text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_name = 'live_cooldowns'"
        ))).all()
    assert len(rows) == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_live_cooldowns_pk_is_user_id_symbol() -> None:
    engine = create_async_engine(_DSN)
    async with engine.connect() as conn:
        rows = (await conn.execute(sa.text(
            "SELECT a.attname "
            "FROM pg_index i JOIN pg_attribute a "
            "  ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
            "WHERE i.indrelid = 'live_cooldowns'::regclass AND i.indisprimary "
            "ORDER BY array_position(i.indkey, a.attnum);"
        ))).all()
    cols = [r.attname for r in rows]
    assert cols == ["user_id", "symbol"], (
        f"Expected PK = (user_id, symbol); got {cols}"
    )
    await engine.dispose()


@pytest.mark.asyncio
async def test_live_cooldowns_columns() -> None:
    engine = create_async_engine(_DSN)
    async with engine.connect() as conn:
        rows = (await conn.execute(sa.text(
            "SELECT column_name, data_type, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_name = 'live_cooldowns'"
        ))).all()
    cols = {r.column_name: (r.data_type, r.is_nullable) for r in rows}
    assert set(cols.keys()) == {
        "user_id", "symbol", "cooldown_until",
        "last_exit_reason", "last_mtf_agreement", "updated_at",
    }
    # Nullability contract
    assert cols["user_id"][1] == "NO"
    assert cols["symbol"][1] == "NO"
    assert cols["cooldown_until"][1] == "NO"
    assert cols["last_exit_reason"][1] == "NO"
    assert cols["last_mtf_agreement"][1] == "YES"
    assert cols["updated_at"][1] == "NO"
    # Type contract — Postgres reports these names
    assert cols["cooldown_until"][0] == "timestamp with time zone"
    assert cols["updated_at"][0] == "timestamp with time zone"
    assert cols["last_mtf_agreement"][0] == "smallint"
    await engine.dispose()


@pytest.mark.asyncio
async def test_live_cooldowns_active_partial_index_exists() -> None:
    """Partial index accelerates the /cooldowns endpoint (active-only scan)."""
    engine = create_async_engine(_DSN)
    async with engine.connect() as conn:
        rows = (await conn.execute(sa.text(
            "SELECT indexname FROM pg_indexes "
            "WHERE tablename = 'live_cooldowns' "
            "  AND indexname = 'ix_live_cooldowns_active'"
        ))).all()
    assert len(rows) == 1
    await engine.dispose()
