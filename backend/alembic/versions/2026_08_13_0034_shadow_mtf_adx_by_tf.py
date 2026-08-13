"""Item 4 (2026-08-13): shadow_trades.mtf_adx_by_tf_json.

Adds 1 nullable analytics column to shadow_trades:
  - mtf_adx_by_tf_json: JSONB NULL (Postgres) / TEXT NULL (SQLite)

Per-timeframe ADX map (e.g. {"5m": 18.3, "1h": 31.2}) that the live
dispatch path's entry-quality gate already computes and consults
in-memory (app/core/gates/entry_quality.py) but never persisted
anywhere — so the audit trail for ADX-gate decisions did not exist on
shadow_trades. Recording-only: not part of the hash chain (see
NON_HASHED_ALLOW_LIST in app/db/audit.py), same treatment as the PR1
7-column set added in migration 0020.

Scoped to shadow_trades only. predictions and live_trades have the
same in-memory-only gap but are out of scope for this pass — flagged
for a future PR, ideally superseded by the persisted dispatch decision
log (item 3, 2026-08-13, design pending).

Revision ID: 0034_shadow_mtf_adx_by_tf
Revises: 0033_oi_24h_delta
Create Date: 2026-08-13
"""
from collections.abc import Sequence

from alembic import op


revision: str = "0034_shadow_mtf_adx_by_tf"
down_revision: str | None = "0033_oi_24h_delta"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    is_pg = dialect.startswith("postgres")
    json_type = "JSONB" if is_pg else "TEXT"
    op.execute(
        f"ALTER TABLE shadow_trades ADD COLUMN mtf_adx_by_tf_json {json_type} NULL;"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE shadow_trades DROP COLUMN IF EXISTS mtf_adx_by_tf_json;"
    )
