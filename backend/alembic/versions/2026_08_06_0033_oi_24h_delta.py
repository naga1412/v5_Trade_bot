"""flow_feature_snapshots.oi_24h_delta — item 4 forward-validation collection

Operator follow-up to the item-1 decisive OI analysis (2026-08-06): the
existing oi_4h_delta showed real but weak separation (best precision ~32%
at 15.5% recall vs a 22.48% base rate). Adds a second, longer window
(24h) collected from the same already-fetched openInterestHist response
(zero new API cost) so the finding can be validated FORWARD rather than
only retrospectively. Storage only — not read by any scoring or gating
path.

Revision ID: 0033_oi_24h_delta
Revises: 0032_flow_feature_snapshots
Create Date: 2026-08-06
"""
from collections.abc import Sequence

from alembic import op


revision: str = "0033_oi_24h_delta"
down_revision: str | None = "0032_flow_feature_snapshots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE flow_feature_snapshots "
        "ADD COLUMN oi_24h_delta DOUBLE PRECISION;"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE flow_feature_snapshots DROP COLUMN IF EXISTS oi_24h_delta;"
    )
