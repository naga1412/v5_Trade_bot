"""PR-FIX-PR275-FOLLOWUP-CHECKVIOLATION (2026-05-27): extend
`live_trades_approved_via_check` to include 'telegram' and 'hybrid'.

ROOT CAUSE
==========
Migration 0016 (2026-05-09, sp8_trading_tables) shipped the constraint
with three allowed values: ('telegram-button', 'auto', 'manual-button').
The code path in `_place_approved_order`
(`app/ops/telegram_polling.py`) has ALWAYS written
`approved_via='telegram'` (no `-button` suffix) — see commit `0222129`
(refactor to `build_live_trade_payload`, predates PR #275). Every
attempt at the telegram-approve path silently CheckViolation'd in
Postgres, then in PR #275's new pending->open lifecycle the failure
surfaces as a Phase 1 INSERT failure (visible, bounded — net win) but
still blocks the path.

This migration adds `'telegram'` (matches code reality) and `'hybrid'`
(defensive — reserves the value for a future PR that may distinguish
the hybrid auto-execute path from straight `'auto'`).

CONSTRAINT TRANSITIONS
======================
Pre-0029:  CHECK (approved_via IN ('telegram-button', 'auto', 'manual-button'))
Post-0029: CHECK (approved_via IN ('telegram-button', 'auto',
                                   'manual-button', 'telegram', 'hybrid'))

Existing rows (only id=1, the manual_cleanup_stale_row) carry
'manual-button' so the new constraint accepts them unchanged.

Revision ID: 0029_live_trades_approved_via
Revises: 0028_live_trades_lifecycle
Create Date: 2026-05-27
"""
from collections.abc import Sequence

from alembic import op


revision: str = "0029_live_trades_approved_via"
down_revision: str | None = "0028_live_trades_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_UP_ALLOWED = (
    "telegram-button",
    "auto",
    "manual-button",
    "telegram",
    "hybrid",
)
_DOWN_ALLOWED = ("telegram-button", "auto", "manual-button")


def _array_sql(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect != "postgresql":
        # SQLite test fixtures define live_trades via raw CREATE TABLE
        # without the CHECK; nothing to migrate.
        return

    op.execute(
        "ALTER TABLE live_trades "
        "DROP CONSTRAINT IF EXISTS live_trades_approved_via_check"
    )
    op.execute(
        "ALTER TABLE live_trades "
        "ADD CONSTRAINT live_trades_approved_via_check "
        f"CHECK (approved_via IN ({_array_sql(_UP_ALLOWED)}))"
    )


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect != "postgresql":
        return

    op.execute(
        "ALTER TABLE live_trades "
        "DROP CONSTRAINT IF EXISTS live_trades_approved_via_check"
    )
    op.execute(
        "ALTER TABLE live_trades "
        "ADD CONSTRAINT live_trades_approved_via_check "
        f"CHECK (approved_via IN ({_array_sql(_DOWN_ALLOWED)}))"
    )
