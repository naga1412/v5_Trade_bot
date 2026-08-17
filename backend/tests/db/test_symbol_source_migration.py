from __future__ import annotations

import os

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL", "").startswith("postgresql"),
    reason="Postgres DATABASE_URL not set — migration tests are CI-only.",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("table", ["predictions", "shadow_trades", "telegram_signals", "live_trades"])
async def test_symbol_source_column_defaults_established_top20(table: str) -> None:
    """Existing rows backfill to 'established_top20' -- they were all
    covered under the pre-2026-08-15 top-N-by-volume selector, which is
    what that tag denotes (a lineage marker, not a live-recomputed rank)."""
    engine = create_async_engine(os.environ["DATABASE_URL"])
    async with engine.begin() as conn:
        col = (await conn.execute(sa.text(
            "SELECT column_name, column_default, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = 'symbol_source'"
        ), {"t": table})).one()
        assert col.is_nullable == "NO"
        assert "established_top20" in col.column_default
    await engine.dispose()
