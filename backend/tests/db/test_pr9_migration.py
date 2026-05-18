"""Migration tests for 0023_pr9_users_balance_tier.

Verifies the schema introduced by the PR9 migration:
  - users.balance_tier column exists, NOT NULL, DEFAULT 'small'.
  - Existing rows backfilled to 'small'.

Postgres-only. SQLite tests skip via the postgres_db guard.
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
    reason="Postgres DATABASE_URL not set — migration tests are CI-only.",
)


@pytest.mark.asyncio
async def test_users_balance_tier_column_exists() -> None:
    engine = create_async_engine(_DSN)
    async with engine.connect() as conn:
        rows = (await conn.execute(sa.text(
            "SELECT column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns "
            "WHERE table_name = 'users' AND column_name = 'balance_tier'"
        ))).all()
    assert len(rows) == 1
    col = rows[0]
    assert col.is_nullable == "NO"
    # Postgres reports VARCHAR as 'character varying'
    assert col.data_type in ("character varying", "text")
    # column_default looks like "'small'::character varying" in Postgres
    assert "small" in (col.column_default or "")
    await engine.dispose()


@pytest.mark.asyncio
async def test_users_balance_tier_backfilled_to_small() -> None:
    """Existing rows seeded by 0005_user_id_columns should now carry
    balance_tier='small'. Catches a migration that creates the column
    but forgets the backfill step."""
    engine = create_async_engine(_DSN)
    async with engine.connect() as conn:
        rows = (await conn.execute(sa.text(
            "SELECT balance_tier FROM users WHERE balance_tier IS NULL"
        ))).all()
    assert rows == [], (
        f"users with NULL balance_tier post-migration: {len(rows)}; "
        "backfill step is missing or skipped"
    )
    await engine.dispose()
