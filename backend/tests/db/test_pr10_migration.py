"""Migration tests for 0024_pr10_symbol_perf_snapshots."""
from __future__ import annotations

import os
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

_DSN = os.environ.get("DATABASE_URL", "")
_IS_PG = _DSN.startswith("postgresql")

pytestmark = pytest.mark.skipif(not _IS_PG, reason="Postgres-only")


@pytest.mark.asyncio
async def test_symbol_performance_snapshots_table_exists() -> None:
    engine = create_async_engine(_DSN)
    async with engine.connect() as conn:
        rows = (await conn.execute(sa.text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_name = 'symbol_performance_snapshots'"
        ))).all()
    assert len(rows) == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_symbol_performance_snapshots_columns() -> None:
    engine = create_async_engine(_DSN)
    async with engine.connect() as conn:
        rows = (await conn.execute(sa.text(
            "SELECT column_name, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'symbol_performance_snapshots'"
        ))).all()
    cols = {r.column_name: r.is_nullable for r in rows}
    assert "id" in cols
    assert "symbol" in cols and cols["symbol"] == "NO"
    assert "window_start" in cols and cols["window_start"] == "NO"
    assert "window_end" in cols and cols["window_end"] == "NO"
    assert "trades_count" in cols and cols["trades_count"] == "NO"
    assert "win_rate" in cols and cols["win_rate"] == "YES"
    assert "sharpe" in cols and cols["sharpe"] == "YES"
    assert "allowed" in cols and cols["allowed"] == "NO"
    assert "computed_at" in cols and cols["computed_at"] == "NO"
    assert "prev_hash" in cols and cols["prev_hash"] == "NO"
    assert "row_hash" in cols and cols["row_hash"] == "NO"
    await engine.dispose()


@pytest.mark.asyncio
async def test_symbol_performance_snapshots_index() -> None:
    engine = create_async_engine(_DSN)
    async with engine.connect() as conn:
        rows = (await conn.execute(sa.text(
            "SELECT indexname FROM pg_indexes "
            "WHERE tablename = 'symbol_performance_snapshots' "
            "AND indexname = 'ix_symbol_perf_symbol_computed'"
        ))).all()
    assert len(rows) == 1
    await engine.dispose()
