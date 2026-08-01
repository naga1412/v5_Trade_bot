"""shadow_trade_variants — paired-comparison variant lane

Amendment 3 (2026-07-31) to breakeven-stop design. Each base
shadow_trades row can spawn N variant rows, one per active trigger
in `SHADOW_BREAKEVEN_VARIANT_TRIGGERS_R`. Variant rows are DERIVED
from bar history replayed through `simulate_variant_exit`; they are
NOT hash-chained (buffer isn't audit-critical, base row is chained).

FK: `base_shadow_trade_id -> shadow_trades(id) ON DELETE CASCADE`.
UNIQUE(base_shadow_trade_id, variant_name) prevents duplicate variant
rows if the worker retries a persist.

Revision ID: 0031_shadow_trade_variants
Revises: 0030_healer_findings
Create Date: 2026-07-31
"""
from collections.abc import Sequence

from alembic import op


revision: str = "0031_shadow_trade_variants"
down_revision: str | None = "0030_healer_findings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE shadow_trade_variants (
            id BIGSERIAL PRIMARY KEY,
            base_shadow_trade_id BIGINT NOT NULL
                REFERENCES shadow_trades(id) ON DELETE CASCADE,
            variant_name TEXT NOT NULL,
            trigger_r NUMERIC(6, 4) NOT NULL,
            armed BOOLEAN NOT NULL,
            exit_price NUMERIC(20, 10) NOT NULL,
            exit_reason TEXT NOT NULL
                CHECK (exit_reason IN
                    ('TAKE_PROFIT', 'STOP_LOSS', 'TIMEOUT', 'BREAKEVEN')),
            exit_ts TIMESTAMPTZ NOT NULL,
            bars_held INT NOT NULL,
            pnl_pct NUMERIC(10, 6) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (base_shadow_trade_id, variant_name)
        );
        """
    )
    op.execute(
        "CREATE INDEX shadow_trade_variants_base_idx "
        "ON shadow_trade_variants (base_shadow_trade_id);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS shadow_trade_variants_base_idx;")
    op.execute("DROP TABLE IF EXISTS shadow_trade_variants;")
