"""PR-FIX-PR264-RESPONSE-CHECK-CONSTRAINT-AND-AUTOSKIP-DATETIME

PR #264 introduced three new response outcomes for telegram_signals —
'stale_price' (drift guard rejected the approval), 'auto_skipped' (the
server-side timeout worker fired), 'approve_lost_race' (a second
operator click after the row resolved) — but did not extend the inline
CHECK constraint on the `response` column. Every attempt to write one
of those values failed with CheckViolationError, leaving rows stuck at
'approved' with `resulted_in_trade_id=NULL` (drift) and the auto-skip
worker firing every 30s logging "DB error (failing open)" for 6h
straight.

This migration drops the auto-named `telegram_signals_response_check`
constraint and re-creates it with the expanded ARRAY. The new name is
explicit so the down migration can target it reliably.

Revision ID: 0027_widen_response_check
Revises: 0026_symbol_halt_state
Create Date: 2026-05-26
"""
from collections.abc import Sequence

from alembic import op


revision: str = "0027_widen_response_check"
down_revision: str | None = "0026_symbol_halt_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_UP_ALLOWED = (
    "approved",
    "skipped",
    "timeout",
    "error",
    "stale_price",
    "auto_skipped",
    "approve_lost_race",
)
_DOWN_ALLOWED = ("approved", "skipped", "timeout", "error")


def _array_sql(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect != "postgresql":
        # SQLite test fixtures define telegram_signals via raw CREATE TABLE
        # without a CHECK constraint (see test_telegram_polling.py + the
        # SQLite branch of 0016_sp8_trading_tables). Nothing to migrate.
        return

    op.execute(
        "ALTER TABLE telegram_signals "
        "DROP CONSTRAINT telegram_signals_response_check"
    )
    op.execute(
        "ALTER TABLE telegram_signals "
        "ADD CONSTRAINT telegram_signals_response_check "
        f"CHECK (response IN ({_array_sql(_UP_ALLOWED)}))"
    )


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect != "postgresql":
        return

    op.execute(
        "ALTER TABLE telegram_signals "
        "DROP CONSTRAINT telegram_signals_response_check"
    )
    op.execute(
        "ALTER TABLE telegram_signals "
        "ADD CONSTRAINT telegram_signals_response_check "
        f"CHECK (response IN ({_array_sql(_DOWN_ALLOWED)}))"
    )
