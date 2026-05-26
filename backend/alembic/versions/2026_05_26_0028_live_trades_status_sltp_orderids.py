"""PR-FIX-GHOST-POSITIONS-ATOMIC-SLTP (2026-05-26): pending->open lifecycle
on live_trades + Binance-native SL/TP order IDs.

The old flow placed a Binance MARKET order, THEN INSERTed the live_trades
row. If the INSERT raised after the Binance call succeeded (audit-chain
race, asyncpg disconnect, schema drift), the Binance position was left
open with no DB record — invisible to live_exit_monitor, unreachable by
the close path. Operator had to manually cleanup.

The new flow pre-INSERTs a `status='pending'` row, calls Binance with
market+SL+TP atomically (emergency close on partial failure), then
UPDATEs to `status='open'` with the order IDs. Migrated rows default
to `status='closed'` so they don't trip the reconciler in
live_exit_monitor.

Columns added (all non-hashed per app/db/audit.py — these are
post-decision lifecycle/identifier columns, not signal-integrity
inputs):

  * status            text NOT NULL DEFAULT 'closed'
                      CHECK (status IN ('pending','open','failed','closed'))
                      Backfilled to 'closed' on existing rows so they
                      look terminal to the reconciler.
  * sl_order_id       text NULL  — Binance STOP_MARKET order id (filled
                      on `status='open'`)
  * tp_order_id       text NULL  — Binance TAKE_PROFIT_MARKET order id
  * failure_reason    text NULL  — populated when status='failed' to
                      explain WHY (Binance error string, emergency-close
                      result, etc.)

Revision ID: 0028_live_trades_status_sltp_orderids
Revises: 0027_widen_response_check
Create Date: 2026-05-26
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0028_live_trades_status_sltp_orderids"
down_revision: str | None = "0027_widen_response_check"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_STATUS_VALUES = ("pending", "open", "failed", "closed")


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    # 1. Add the 4 new columns (nullable first so existing rows survive).
    op.add_column(
        "live_trades",
        sa.Column("status", sa.Text(), nullable=True),
    )
    op.add_column(
        "live_trades",
        sa.Column("sl_order_id", sa.Text(), nullable=True),
    )
    op.add_column(
        "live_trades",
        sa.Column("tp_order_id", sa.Text(), nullable=True),
    )
    op.add_column(
        "live_trades",
        sa.Column("failure_reason", sa.Text(), nullable=True),
    )

    # 2. Backfill status='closed' on every existing row so the
    # reconciler in live_exit_monitor treats them as terminal (won't
    # try to look up a Binance position for an old row). NULL would
    # also fail the upcoming NOT NULL constraint.
    op.execute("UPDATE live_trades SET status = 'closed' WHERE status IS NULL")

    # 3. Promote status to NOT NULL with default 'closed' (so future
    # raw-SQL inserts that omit it land terminal-safe).
    op.alter_column(
        "live_trades",
        "status",
        existing_type=sa.Text(),
        nullable=False,
        server_default=sa.text("'closed'"),
    )

    # 4. CHECK constraint on the allowed status values. Postgres only —
    # SQLite test fixtures don't enforce CHECK on ALTER TABLE for new
    # columns anyway, and the production guard is what matters.
    if dialect == "postgresql":
        op.execute(
            "ALTER TABLE live_trades "
            "ADD CONSTRAINT live_trades_status_check "
            f"CHECK (status IN ({', '.join(repr(v) for v in _STATUS_VALUES)}))"
        )


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        op.execute(
            "ALTER TABLE live_trades DROP CONSTRAINT IF EXISTS "
            "live_trades_status_check"
        )

    op.drop_column("live_trades", "failure_reason")
    op.drop_column("live_trades", "tp_order_id")
    op.drop_column("live_trades", "sl_order_id")
    op.drop_column("live_trades", "status")
