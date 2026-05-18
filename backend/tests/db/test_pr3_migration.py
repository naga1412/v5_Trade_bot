"""Migration tests for 0021_pr3_shadow_per_tf.

Verifies the schema changes introduced by the PR3 migration:
  - shadow_cooldowns PK extended from (user_id, symbol) to
    (user_id, symbol, timeframe); column DEFAULT '1h'.
  - shadow_open_positions UNIQUE moved from (symbol) to
    (symbol, timeframe); column DEFAULT '1h'.
  - G1 columns added (recording-only, NULL-able):
      shadow_trades.hold_scaling_factor REAL
      shadow_trades.hold_timeout_bars SMALLINT
      live_trades.hold_scaling_factor REAL
      live_trades.hold_timeout_bars SMALLINT

Postgres-only. SQLite tests skip via the `postgres_db` guard (mirrors
test_pr1_migration.py pattern). CI runs `alembic upgrade head` before
pytest against a Postgres test DB.
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
async def test_shadow_cooldowns_pk_extended_with_timeframe() -> None:
    """PK columns should be (user_id, symbol, timeframe) in declared order."""
    engine = create_async_engine(_DSN)
    async with engine.connect() as conn:
        rows = (await conn.execute(sa.text(
            "SELECT a.attname "
            "FROM pg_index i JOIN pg_attribute a "
            "  ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
            "WHERE i.indrelid = 'shadow_cooldowns'::regclass AND i.indisprimary "
            "ORDER BY array_position(i.indkey, a.attnum);"
        ))).all()
    cols = [r.attname for r in rows]
    assert cols == ["user_id", "symbol", "timeframe"], (
        f"Expected PK = (user_id, symbol, timeframe); got {cols}"
    )
    await engine.dispose()


@pytest.mark.asyncio
async def test_shadow_open_positions_unique_is_symbol_timeframe() -> None:
    """UNIQUE constraint should be (symbol, timeframe), not just (symbol)."""
    engine = create_async_engine(_DSN)
    async with engine.connect() as conn:
        rows = (await conn.execute(sa.text(
            "SELECT conname, pg_get_constraintdef(oid) AS defn "
            "FROM pg_constraint "
            "WHERE conrelid = 'shadow_open_positions'::regclass "
            "  AND contype = 'u';"
        ))).all()
    matched = [r for r in rows if "(symbol, timeframe)" in r.defn]
    assert matched, f"Expected (symbol, timeframe) UNIQUE; got {[(r.conname, r.defn) for r in rows]}"
    # And the old (symbol) UNIQUE must be gone:
    old = [r for r in rows if r.defn == "UNIQUE (symbol)"]
    assert not old, f"Old (symbol) UNIQUE still present: {old}"
    await engine.dispose()


@pytest.mark.asyncio
async def test_shadow_cooldowns_timeframe_default_is_1h() -> None:
    """The timeframe column should default to '1h' so any pre-PR3 code path
    that inserts without specifying timeframe continues to work."""
    engine = create_async_engine(_DSN)
    async with engine.connect() as conn:
        row = (await conn.execute(sa.text(
            "SELECT column_default FROM information_schema.columns "
            "WHERE table_name='shadow_cooldowns' AND column_name='timeframe';"
        ))).first()
    assert row is not None, "shadow_cooldowns.timeframe column missing"
    assert "1h" in (row.column_default or ""), (
        f"Expected DEFAULT '1h'; got {row.column_default!r}"
    )
    await engine.dispose()


@pytest.mark.asyncio
async def test_shadow_open_positions_timeframe_default_is_1h() -> None:
    engine = create_async_engine(_DSN)
    async with engine.connect() as conn:
        row = (await conn.execute(sa.text(
            "SELECT column_default FROM information_schema.columns "
            "WHERE table_name='shadow_open_positions' AND column_name='timeframe';"
        ))).first()
    assert row is not None
    assert "1h" in (row.column_default or "")
    await engine.dispose()


# --- G1 scaling columns ----------------------------------------------------


@pytest.mark.asyncio
async def test_shadow_trades_has_hold_scaling_columns() -> None:
    """Spec §4.6b: shadow_trades gets hold_scaling_factor REAL NULL +
    hold_timeout_bars SMALLINT NULL. Both nullable — populated only when
    HOLD_TP_SCALING_ENABLED=True."""
    engine = create_async_engine(_DSN)
    async with engine.connect() as conn:
        rows = (await conn.execute(sa.text(
            "SELECT column_name, data_type, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_name='shadow_trades' "
            "  AND column_name IN ('hold_scaling_factor', 'hold_timeout_bars');"
        ))).all()
    cols = {r.column_name: (r.data_type, r.is_nullable) for r in rows}
    assert "hold_scaling_factor" in cols, f"missing hold_scaling_factor; got {cols}"
    assert "hold_timeout_bars" in cols, f"missing hold_timeout_bars; got {cols}"
    assert cols["hold_scaling_factor"][1] == "YES", "hold_scaling_factor must be NULL-able"
    assert cols["hold_timeout_bars"][1] == "YES", "hold_timeout_bars must be NULL-able"
    # data_type: 'real' for REAL, 'smallint' for SMALLINT.
    assert cols["hold_scaling_factor"][0] == "real"
    assert cols["hold_timeout_bars"][0] == "smallint"
    await engine.dispose()


@pytest.mark.asyncio
async def test_live_trades_has_hold_scaling_columns() -> None:
    """Spec §4.6b: live_trades gets the same 2 columns reserved for a
    future PR that wires the auto-path populate. PR3 itself does NOT
    populate them on live_trades."""
    engine = create_async_engine(_DSN)
    async with engine.connect() as conn:
        rows = (await conn.execute(sa.text(
            "SELECT column_name, data_type, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_name='live_trades' "
            "  AND column_name IN ('hold_scaling_factor', 'hold_timeout_bars');"
        ))).all()
    cols = {r.column_name: (r.data_type, r.is_nullable) for r in rows}
    assert "hold_scaling_factor" in cols
    assert "hold_timeout_bars" in cols
    assert cols["hold_scaling_factor"][1] == "YES"
    assert cols["hold_timeout_bars"][1] == "YES"
    await engine.dispose()


@pytest.mark.asyncio
async def test_pr3_backfill_set_existing_rows_to_1h() -> None:
    """Pre-migration rows in shadow_cooldowns + shadow_open_positions
    should have timeframe='1h' after the backfill step. We can't tell
    pre/post apart at this point (post-migration), but we can assert
    no NULL rows exist (NOT NULL was enforced)."""
    engine = create_async_engine(_DSN)
    async with engine.connect() as conn:
        cooldowns_nulls = (await conn.execute(sa.text(
            "SELECT COUNT(*) AS n FROM shadow_cooldowns WHERE timeframe IS NULL;"
        ))).scalar()
        positions_nulls = (await conn.execute(sa.text(
            "SELECT COUNT(*) AS n FROM shadow_open_positions WHERE timeframe IS NULL;"
        ))).scalar()
    assert cooldowns_nulls == 0, f"shadow_cooldowns has {cooldowns_nulls} NULL timeframe rows"
    assert positions_nulls == 0, f"shadow_open_positions has {positions_nulls} NULL timeframe rows"
    await engine.dispose()
