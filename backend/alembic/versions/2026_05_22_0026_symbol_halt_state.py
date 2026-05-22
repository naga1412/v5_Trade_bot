"""PR-SAFETY-BATCH-1 FU-33: symbol_halt_state circuit-breaker table.

One row per symbol. Stores the timestamp until which new entries on
that symbol are blocked + a forensic ``reason`` string. Not hash-chained
(operational state, not audit data) — therefore NOT registered in
``app/db/audit.py::HASH_PAYLOAD_COLUMNS``.

UPSERT pattern: ``check_slippage`` uses ``INSERT ... ON CONFLICT (symbol)
DO UPDATE`` so the latest halt overwrites the previous row for the same
symbol. Expired rows are kept in place (operator audit trail);
``is_symbol_halted`` filters on ``halted_until > now()``.

Revision ID: 0026_symbol_halt_state
Revises: 0025_pr_plumbing_1_op_pr1_cols
Create Date: 2026-05-22
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0026_symbol_halt_state"
down_revision: str | None = "0025_pr_plumbing_1_op_pr1_cols"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "symbol_halt_state",
        sa.Column("symbol", sa.Text(), primary_key=True),
        sa.Column(
            "halted_until", sa.TIMESTAMP(timezone=True), nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("symbol_halt_state")
