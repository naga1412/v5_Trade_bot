"""PR10: symbol_performance_snapshots — daily per-symbol Sharpe + allowlist snapshot.

8th hash-chained audit table. Append-only. Single writer (daily worker)
means FU-24's concurrent-insert race doesn't fire here.

Revision ID: 0024_pr10_symbol_performance_snapshots
Revises: 0023_pr9_users_balance_tier
Create Date: 2026-05-19
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_pr10_symbol_performance_snapshots"
down_revision: str | None = "0023_pr9_users_balance_tier"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "symbol_performance_snapshots",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trades_count", sa.Integer, nullable=False),
        sa.Column("win_rate", sa.Float, nullable=True),
        sa.Column("sharpe", sa.Float, nullable=True),
        sa.Column("allowed", sa.Boolean, nullable=False),
        sa.Column(
            "computed_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column("prev_hash", sa.Text, nullable=False),
        sa.Column("row_hash", sa.Text, nullable=False, unique=True),
        sa.Column("inputs_hash", sa.Text, nullable=True),
        sa.CheckConstraint("trades_count >= 0", name="ck_trades_count_nonneg"),
    )
    op.create_index(
        "ix_symbol_perf_symbol_computed",
        "symbol_performance_snapshots",
        ["symbol", sa.text("computed_at DESC")],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_symbol_perf_symbol_computed",
        table_name="symbol_performance_snapshots",
    )
    op.drop_table("symbol_performance_snapshots")
