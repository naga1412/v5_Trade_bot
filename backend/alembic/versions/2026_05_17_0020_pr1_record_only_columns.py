"""PR1: record-only analytics columns + timeframe NOT NULL on live_trades.

Adds 7 nullable analytics columns to predictions, shadow_trades, live_trades:
  - mtf_agreement: SMALLINT NULL
  - mtf_dominant_tf: VARCHAR(8) NULL
  - mtf_directions_json: JSONB NULL (Postgres) / TEXT NULL (SQLite)
  - p_win: REAL NULL
  - effective_score: REAL NULL
  - realized_vol_20d: REAL NULL
  - funding_directional_adj: REAL NULL

Enforces timeframe NOT NULL on live_trades (it was missing this column
entirely; 3-step add → backfill '1h' → alter NOT NULL).
predictions.timeframe and shadow_trades.timeframe were ALREADY NOT NULL
since their initial schema; this migration is a no-op for them.

Adds composite index (symbol, timeframe, <primary_ts>) on each table:
  - predictions (symbol, timeframe, ts DESC)
  - shadow_trades (symbol, timeframe, opened_at DESC)
  - live_trades (symbol, timeframe, opened_at DESC)

Note: operator's spec suggested (symbol, timeframe, created_at), but
created_at doesn't exist on any of these tables. Adapted to use each
table's existing primary timestamp column instead:
  predictions -> ts, shadow_trades -> opened_at, live_trades -> opened_at.

Row counts at migration time:
  predictions: 95
  shadow_trades: 20
  live_trades: 1
No chunking needed for the UPDATE backfill.

Revision ID: 0020_pr1_record_only_columns
Revises: 0019_shadow_observations
Create Date: 2026-05-17
"""
from collections.abc import Sequence

from alembic import op


revision: str = "0020_pr1_record_only_columns"
down_revision: str | None = "0019_shadow_observations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLES = ("predictions", "shadow_trades", "live_trades")

_TS_COLUMN_PER_TABLE = {
    "predictions": "ts",
    "shadow_trades": "opened_at",
    "live_trades": "opened_at",
}

# Non-JSON analytics columns: (name, pg_type, sqlite_type)
_SCALAR_COLUMNS = (
    ("mtf_agreement",           "SMALLINT",            "INTEGER"),
    ("mtf_dominant_tf",         "VARCHAR(8)",           "TEXT"),
    ("p_win",                   "REAL",                "REAL"),
    ("effective_score",         "REAL",                "REAL"),
    ("realized_vol_20d",        "REAL",                "REAL"),
    ("funding_directional_adj", "REAL",                "REAL"),
)


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    is_pg = dialect.startswith("postgres")
    json_type = "JSONB" if is_pg else "TEXT"

    # Step 1: live_trades.timeframe — add column (does NOT exist yet),
    # backfill '1h', then alter to NOT NULL.
    # predictions.timeframe and shadow_trades.timeframe are already NOT NULL
    # from their initial schema — no migration step needed for them.
    op.execute(
        "ALTER TABLE live_trades ADD COLUMN timeframe VARCHAR(8) NULL;"
    )
    op.execute(
        "UPDATE live_trades SET timeframe = '1h' WHERE timeframe IS NULL;"
    )
    if is_pg:
        op.execute(
            "ALTER TABLE live_trades ALTER COLUMN timeframe SET NOT NULL;"
        )
        op.execute(
            "ALTER TABLE live_trades ALTER COLUMN timeframe SET DEFAULT '1h';"
        )
    else:
        # SQLite does not support ALTER COLUMN; the column stays nullable but
        # the backfill above guarantees all existing rows have '1h'.
        # New rows must supply a value — enforced at the application layer.
        pass

    # Step 2: add 7 new nullable analytics columns to each of the 3 tables.
    for table in _TABLES:
        for col_name, pg_type, sqlite_type in _SCALAR_COLUMNS:
            col_type = pg_type if is_pg else sqlite_type
            op.execute(
                f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type} NULL;"
            )
        # mtf_directions_json is dialect-dependent (JSONB vs TEXT)
        op.execute(
            f"ALTER TABLE {table} ADD COLUMN mtf_directions_json {json_type} NULL;"
        )

    # Step 3: composite index (symbol, timeframe, <primary_ts>) per table.
    for table in _TABLES:
        ts_col = _TS_COLUMN_PER_TABLE[table]
        op.execute(
            f"CREATE INDEX {table}_symbol_tf_{ts_col}_idx "
            f"ON {table} (symbol, timeframe, {ts_col} DESC);"
        )


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    is_pg = dialect.startswith("postgres")

    # Step 3 reverse: drop indexes
    for table in _TABLES:
        ts_col = _TS_COLUMN_PER_TABLE[table]
        op.execute(
            f"DROP INDEX IF EXISTS {table}_symbol_tf_{ts_col}_idx;"
        )

    # Step 2 reverse: drop 7 analytics columns from each table.
    # SQLite 3.35+ supports DROP COLUMN — matches pattern in 0007 migration.
    _all_new_cols = (
        "mtf_agreement",
        "mtf_dominant_tf",
        "mtf_directions_json",
        "p_win",
        "effective_score",
        "realized_vol_20d",
        "funding_directional_adj",
    )
    for table in _TABLES:
        for col_name in _all_new_cols:
            op.execute(
                f"ALTER TABLE {table} DROP COLUMN IF EXISTS {col_name};"
            )

    # Step 1 reverse: drop live_trades.timeframe entirely.
    # predictions and shadow_trades.timeframe are NOT touched —
    # their NOT NULL came from initial schema.
    op.execute(
        "ALTER TABLE live_trades DROP COLUMN IF EXISTS timeframe;"
    )
