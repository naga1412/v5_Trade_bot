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
@pytest.mark.parametrize("table", ["predictions", "telegram_signals", "live_trades"])
async def test_symbol_source_column_defaults_established_top20(table: str) -> None:
    """Existing rows backfill to 'established_top20' -- they were all
    covered under the pre-2026-08-15 top-N-by-volume selector, which is
    what that tag denotes (a lineage marker, not a live-recomputed rank).

    shadow_trades deliberately dropped from this parametrize list:
    migration 0042 (item 0, 2026-08-30) removed both NOT NULL and the
    DEFAULT on shadow_trades.symbol_source (and shadow_open_positions',
    which was never covered by this test) so a genuine classification
    failure at position-open time can write a real, honest NULL instead
    of a guessed cohort. See tests/db/test_symbol_source_nullable_
    migration.py for the up-to-date assertions on both of those tables.
    predictions/telegram_signals/live_trades are untouched -- their
    symbol_source is a different write path (live_prediction.py /
    dispatcher), out of item 0's scope, and still carries the original
    NOT NULL DEFAULT 'established_top20' unchanged."""
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
