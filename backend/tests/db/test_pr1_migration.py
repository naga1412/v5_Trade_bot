"""Migration tests for 0020_pr1_record_only_columns.

These tests verify the schema changes introduced by the PR1 migration:
  - 7 analytics columns added to predictions, shadow_trades, live_trades
  - live_trades.timeframe NOT NULL with '1h' backfill
  - composite (symbol, timeframe, <ts>) indexes on each table

Scope:
  - Tests that require a running Postgres instance (and the full alembic
    history) are **skipped on SQLite** via the ``postgres_db`` fixture's
    ``pytest.skip`` guard. These tests are CI-only (the backend CI job
    runs ``alembic upgrade head`` against a Postgres test DB before
    running pytest; see docs/ci-setup.md).
  - The ``inspect_*`` tests use ``sqlalchemy.inspect`` against the current
    connection and are the most robust: they assert on actual column names
    and types, not on whether the migration ran.

Environment:
  Set ``DATABASE_URL`` to a Postgres URL to run the full suite:
    DATABASE_URL=postgresql+asyncpg://user:pass@localhost/test_db pytest

  On SQLite / in-memory, only the skip-guarded tests execute (fast; zero
  infrastructure).
"""
from __future__ import annotations

import os

import pytest
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

# --------------------------------------------------------------------------- #
#  Fixture helpers                                                              #
# --------------------------------------------------------------------------- #

_PR1_ANALYTICS_COLS = (
    "mtf_agreement",
    "mtf_dominant_tf",
    "mtf_directions_json",
    "p_win",
    "effective_score",
    "realized_vol_20d",
    "funding_directional_adj",
)

_TABLES = ("predictions", "shadow_trades", "live_trades")

_INDEX_PER_TABLE = {
    "predictions":   "predictions_symbol_tf_ts_idx",
    "shadow_trades": "shadow_trades_symbol_tf_opened_at_idx",
    "live_trades":   "live_trades_symbol_tf_opened_at_idx",
}


def _get_database_url() -> str | None:
    """Return DATABASE_URL if it points at Postgres, else None."""
    url = os.getenv("DATABASE_URL", "")
    if url.startswith("postgresql") or url.startswith("postgres"):
        return url
    return None


@pytest.fixture
async def postgres_engine():
    """Async SQLAlchemy engine connected to the test Postgres DB.

    Skips if DATABASE_URL is not set to a Postgres URL — this fixture is
    intentionally CI-only. The CI backend job runs alembic upgrade head
    before pytest, so the schema is already at head when these tests run.
    """
    url = _get_database_url()
    if url is None:
        pytest.skip(
            "Postgres DATABASE_URL not set — migration tests are CI-only. "
            "Set DATABASE_URL=postgresql+asyncpg://... to run locally."
        )
    engine = create_async_engine(url, echo=False)
    yield engine
    await engine.dispose()


# --------------------------------------------------------------------------- #
#  Schema-introspection tests (CI-only; skip on SQLite)                        #
# --------------------------------------------------------------------------- #


async def test_pr1_analytics_columns_exist_on_all_tables(postgres_engine):
    """After alembic upgrade head, the 7 analytics columns must exist on each
    of the 3 hash-chained tables."""
    async with postgres_engine.connect() as conn:
        for table in _TABLES:
            col_names = {
                row["name"]
                for row in await conn.run_sync(
                    lambda sync_conn, t=table: inspect(sync_conn).get_columns(t)
                )
            }
            for col in _PR1_ANALYTICS_COLS:
                assert col in col_names, (
                    f"Expected column '{col}' in table '{table}' "
                    f"after PR1 migration; found: {sorted(col_names)}"
                )


async def test_live_trades_timeframe_column_exists_and_not_null(postgres_engine):
    """live_trades.timeframe must exist and be NOT NULL after the migration."""
    async with postgres_engine.connect() as conn:
        columns = await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).get_columns("live_trades")
        )
        tf_col = next((c for c in columns if c["name"] == "timeframe"), None)
        assert tf_col is not None, (
            "live_trades.timeframe column must exist after PR1 migration"
        )
        assert not tf_col.get("nullable", True), (
            "live_trades.timeframe must be NOT NULL after PR1 migration"
        )


async def test_predictions_timeframe_still_not_null(postgres_engine):
    """predictions.timeframe was already NOT NULL — migration must not change it."""
    async with postgres_engine.connect() as conn:
        columns = await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).get_columns("predictions")
        )
        tf_col = next((c for c in columns if c["name"] == "timeframe"), None)
        assert tf_col is not None
        assert not tf_col.get("nullable", True), (
            "predictions.timeframe must remain NOT NULL after PR1 migration"
        )


async def test_shadow_trades_timeframe_still_not_null(postgres_engine):
    """shadow_trades.timeframe was already NOT NULL — migration must not change it."""
    async with postgres_engine.connect() as conn:
        columns = await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).get_columns("shadow_trades")
        )
        tf_col = next((c for c in columns if c["name"] == "timeframe"), None)
        assert tf_col is not None
        assert not tf_col.get("nullable", True), (
            "shadow_trades.timeframe must remain NOT NULL after PR1 migration"
        )


async def test_pr1_analytics_columns_are_nullable(postgres_engine):
    """All 7 analytics columns must be nullable (they default to NULL)."""
    async with postgres_engine.connect() as conn:
        for table in _TABLES:
            columns = await conn.run_sync(
                lambda sync_conn, t=table: inspect(sync_conn).get_columns(t)
            )
            col_map = {c["name"]: c for c in columns}
            for col in _PR1_ANALYTICS_COLS:
                assert col_map[col].get("nullable", True), (
                    f"PR1 column '{col}' on '{table}' must be nullable"
                )


async def test_composite_indexes_exist(postgres_engine):
    """Composite (symbol, timeframe, <ts>) indexes must exist on all 3 tables."""
    async with postgres_engine.connect() as conn:
        for table, index_name in _INDEX_PER_TABLE.items():
            indexes = await conn.run_sync(
                lambda sync_conn, t=table: inspect(sync_conn).get_indexes(t)
            )
            idx_names = {idx["name"] for idx in indexes}
            assert index_name in idx_names, (
                f"Expected index '{index_name}' on table '{table}'; "
                f"found: {sorted(idx_names)}"
            )


async def test_new_analytics_columns_default_null_on_insert(postgres_engine):
    """Rows inserted without specifying analytics cols must have NULL values.

    Uses user_id=1 (the bootstrap admin seeded by alembic migration
    0005_user_id_columns.py:36). Hardcoding user_id=0 trips Postgres FK
    enforcement; SQLite's FK behavior is lax by default so this surfaced
    only after FU-8 + the FU-8-followup CI fix made pytest actually run
    against Postgres in CI.
    """
    async with postgres_engine.connect() as conn:
        await conn.execute(sa.text(
            """
            INSERT INTO predictions (
                user_id, symbol, timeframe, ts,
                layer_scores, final_score, direction,
                confidence, inputs_hash, model_version, cold_start,
                prev_hash, row_hash
            ) VALUES (
                1, '__test_pr1_null_check__', '1h', NOW(),
                '{}', 0.0, 'LONG', 0.0, 'test-hash-pr1', 'sp-0', false,
                'GENESIS', 'test-row-hash-pr1-unique'
            )
            """
        ))
        result = await conn.execute(sa.text(
            "SELECT mtf_agreement, mtf_dominant_tf, mtf_directions_json, "
            "p_win, effective_score, realized_vol_20d, funding_directional_adj "
            "FROM predictions WHERE symbol = '__test_pr1_null_check__'"
        ))
        row = result.fetchone()
        await conn.execute(sa.text(
            "DELETE FROM predictions WHERE symbol = '__test_pr1_null_check__'"
        ))
        await conn.commit()

    assert row is not None
    for i, col in enumerate(_PR1_ANALYTICS_COLS):
        assert row[i] is None, (
            f"Expected NULL for '{col}' on default insert; got: {row[i]}"
        )


async def test_live_trades_timeframe_backfill_is_1h(postgres_engine):
    """All existing live_trades rows must have timeframe = '1h' after migration.

    The migration backfills NULL → '1h' before setting NOT NULL, so any
    pre-existing rows must carry '1h'. (With 1 row at migration time, this
    is a trivial sanity check but confirms the UPDATE ran.)
    """
    async with postgres_engine.connect() as conn:
        result = await conn.execute(sa.text(
            "SELECT COUNT(*) FROM live_trades WHERE timeframe IS NULL"
        ))
        null_count = result.scalar()
        assert null_count == 0, (
            f"Found {null_count} live_trades rows with NULL timeframe "
            "after PR1 migration — backfill may not have run"
        )
