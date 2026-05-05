"""trap_enabled — per-asset/per-TF disable flag for noisy traps (SP-5 Phase A)

Revision ID: 0011_trap_enabled
Revises: 0010_universe_adapter_health
Create Date: 2026-05-05
"""
from collections.abc import Sequence

from alembic import op


revision: str = "0011_trap_enabled"
down_revision: str | None = "0010_universe_adapter_health"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE trap_enabled (
            id BIGSERIAL PRIMARY KEY,
            trap_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            disabled_reason TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_by BIGINT REFERENCES users(id),
            UNIQUE (trap_id, symbol, timeframe)
        );
        """
    )
    op.execute(
        "CREATE INDEX trap_enabled_lookup_idx "
        "ON trap_enabled (symbol, timeframe, enabled);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS trap_enabled_lookup_idx;")
    op.execute("DROP TABLE IF EXISTS trap_enabled;")
