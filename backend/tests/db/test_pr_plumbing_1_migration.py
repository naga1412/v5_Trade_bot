"""PR-PLUMBING-1 alembic 0025: migration tests for the 7 PR1 analytics
columns added to ``shadow_open_positions``.

Postgres-only (skipped on SQLite / in-memory). The CI backend job runs
``alembic upgrade head`` against a Postgres test DB before pytest, so the
schema is already at head when these tests run.
"""
from __future__ import annotations

import os

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine


_DSN = os.environ.get("DATABASE_URL", "")
_IS_PG = _DSN.startswith("postgresql") or _DSN.startswith("postgres")

pytestmark = pytest.mark.skipif(
    not _IS_PG,
    reason="Postgres-only — set DATABASE_URL=postgresql+asyncpg://... to run.",
)


_PR1_COLUMNS = (
    "mtf_agreement",
    "mtf_dominant_tf",
    "mtf_directions_json",
    "p_win",
    "effective_score",
    "realized_vol_20d",
    "funding_directional_adj",
)


@pytest.mark.asyncio
async def test_pr_plumbing_1_columns_exist_on_shadow_open_positions() -> None:
    engine = create_async_engine(_DSN)
    async with engine.connect() as conn:
        rows = (await conn.execute(sa.text(
            "SELECT column_name, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_name = 'shadow_open_positions'"
        ))).all()
    cols = {r.column_name: r.is_nullable for r in rows}
    for col in _PR1_COLUMNS:
        assert col in cols, (
            f"Expected column '{col}' on shadow_open_positions after "
            f"PR-PLUMBING-1 migration; found: {sorted(cols)}"
        )
        # All 7 PR1 analytics columns must be nullable.
        assert cols[col] == "YES", f"PR1 column '{col}' must be nullable"
    await engine.dispose()


@pytest.mark.asyncio
async def test_pr_plumbing_1_columns_default_null_on_insert() -> None:
    """A row inserted without PR1 cols must read back as NULLs."""
    engine = create_async_engine(_DSN)
    async with engine.connect() as conn:
        await conn.execute(sa.text(
            """
            INSERT INTO shadow_open_positions (
                user_id, symbol, timeframe, direction,
                entry_price, stop_loss, take_profit, position_size_usdt,
                entry_score, entry_confidence, entry_atr, bars_held,
                opened_at, last_check_at, signal_id
            ) VALUES (
                1, '__test_pr_plumbing_1__', '1h', 'LONG',
                100.0, 95.0, 110.0, 30.0,
                0.5, 0.7, 1.0, 0,
                NOW(), NOW(), '__test_pr_plumbing_1_signal__'
            )
            """
        ))
        result = await conn.execute(sa.text(
            "SELECT mtf_agreement, mtf_dominant_tf, mtf_directions_json, "
            "p_win, effective_score, realized_vol_20d, funding_directional_adj "
            "FROM shadow_open_positions "
            "WHERE symbol = '__test_pr_plumbing_1__'"
        ))
        row = result.fetchone()
        await conn.execute(sa.text(
            "DELETE FROM shadow_open_positions "
            "WHERE symbol = '__test_pr_plumbing_1__'"
        ))
        await conn.commit()
    assert row is not None
    for i, col in enumerate(_PR1_COLUMNS):
        assert row[i] is None, (
            f"Expected NULL for '{col}' on default insert; got: {row[i]}"
        )
    await engine.dispose()
