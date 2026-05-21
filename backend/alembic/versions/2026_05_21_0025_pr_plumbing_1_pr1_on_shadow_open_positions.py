"""PR-PLUMBING-1 Fix 3: persist PR1 analytics columns on shadow_open_positions.

Adds the 7 nullable PR1 analytics columns to ``shadow_open_positions`` so a
process restart between open + close no longer drops them on the closed
``shadow_trades`` row.

The columns match the PR1 migration 0020 schema (predictions / shadow_trades /
live_trades) so the worker can persist + reload them through restart cycles
without a type-coercion step:
  - mtf_agreement           SMALLINT / INTEGER
  - mtf_dominant_tf         VARCHAR(8) / TEXT
  - mtf_directions_json     JSONB / TEXT
  - p_win                   REAL
  - effective_score         REAL
  - realized_vol_20d        REAL
  - funding_directional_adj REAL

``shadow_open_positions`` is NOT a hash-chained audit table (it's not listed
in ``app/db/audit.py::HASH_PAYLOAD_COLUMNS``) — no audit-chain whitelist
change is required. New columns are recording-only state copied from the
PR1 aggregator-hook output on ``LivePredictionOut``.

Operator directive: no backfill — existing rows stay NULL until they close.

Revision ID: 0025_pr_plumbing_1_op_pr1_cols
Revises: 0024_pr10_symbol_perf_snapshots
Create Date: 2026-05-21
"""
from collections.abc import Sequence

from alembic import op


revision: str = "0025_pr_plumbing_1_op_pr1_cols"
down_revision: str | None = "0024_pr10_symbol_perf_snapshots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Non-JSON analytics columns: (name, pg_type, sqlite_type). Matches the
# 0020 PR1 migration so a column's type is bit-identical across the
# 3 hash-chained tables + this 4th recording-only table.
_SCALAR_COLUMNS = (
    ("mtf_agreement",           "SMALLINT",            "INTEGER"),
    ("mtf_dominant_tf",         "VARCHAR(8)",          "TEXT"),
    ("p_win",                   "REAL",                "REAL"),
    ("effective_score",         "REAL",                "REAL"),
    ("realized_vol_20d",        "REAL",                "REAL"),
    ("funding_directional_adj", "REAL",                "REAL"),
)

_ALL_NEW_COLS = (
    "mtf_agreement",
    "mtf_dominant_tf",
    "mtf_directions_json",
    "p_win",
    "effective_score",
    "realized_vol_20d",
    "funding_directional_adj",
)


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    is_pg = dialect.startswith("postgres")
    json_type = "JSONB" if is_pg else "TEXT"

    for col_name, pg_type, sqlite_type in _SCALAR_COLUMNS:
        col_type = pg_type if is_pg else sqlite_type
        op.execute(
            f"ALTER TABLE shadow_open_positions ADD COLUMN {col_name} {col_type} NULL;"
        )
    op.execute(
        f"ALTER TABLE shadow_open_positions ADD COLUMN mtf_directions_json {json_type} NULL;"
    )


def downgrade() -> None:
    # SQLite 3.35+ supports DROP COLUMN — matches the 0020 pattern.
    for col_name in _ALL_NEW_COLS:
        op.execute(
            f"ALTER TABLE shadow_open_positions DROP COLUMN IF EXISTS {col_name};"
        )
