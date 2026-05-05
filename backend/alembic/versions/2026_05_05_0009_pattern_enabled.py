"""pattern_enabled table — per-asset/per-TF disable flag for noisy patterns

Revision ID: 0009_pattern_enabled
Revises: 0008_seed_feature_registry
Create Date: 2026-05-05
"""
from collections.abc import Sequence

from alembic import op


revision: str = "0009_pattern_enabled"
down_revision: str | None = "0008_seed_feature_registry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE pattern_enabled (
            id BIGSERIAL PRIMARY KEY,
            pattern_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            disabled_reason TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_by BIGINT REFERENCES users(id),
            UNIQUE (pattern_id, symbol, timeframe)
        );
        """
    )
    op.execute(
        "CREATE INDEX pattern_enabled_lookup_idx "
        "ON pattern_enabled (symbol, timeframe, enabled);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS pattern_enabled_lookup_idx;")
    op.execute("DROP TABLE IF EXISTS pattern_enabled;")
