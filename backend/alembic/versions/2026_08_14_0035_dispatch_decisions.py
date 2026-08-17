"""Item 3 (2026-08-14): dispatch_decisions -- persisted gate-verdict log.

Gate outcomes were stdout-only, which is why the ADX counterfactual
needed 65 seconds of kline replay, a stale-data risk, and an O(n^2) bug
(fixed separately) -- where a 10ms indexed query should have done.

Adds dispatch_decisions: one row per live dispatch attempt where
dispatch_if_eligible actually ran (i.e. vault loaded + a usable trade
setup existed -- matches the existing log.info gating in
_maybe_dispatch). Records the 4 entry-quality sub-gate verdicts
UNCONDITIONALLY (see app.core.gates.entry_quality.evaluate_all_gates)
plus the real dispatch outcome/detail from dispatcher.dispatch().

Telemetry, not a financial ledger:
  - Append-only, no UPDATE/DELETE call sites planned.
  - Indexed on (symbol, ts) for the query pattern this replaces
    (kline-window lookups keyed by symbol+time).
  - Deliberately NOT hash-chained. Not registered in
    app.db.audit.HASH_PAYLOAD_COLUMNS or NON_HASHED_ALLOW_LIST --
    app.ops.verifier_scheduler._tables_to_verify() iterates
    HASH_PAYLOAD_COLUMNS.keys() only, so this table is structurally
    invisible to the nightly audit-chain verifier (see
    test_dispatch_decisions_ignored_by_audit_verifier).

Revision ID: 0035_dispatch_decisions
Revises: 0034_shadow_mtf_adx_by_tf
Create Date: 2026-08-14
"""
from collections.abc import Sequence

from alembic import op


revision: str = "0035_dispatch_decisions"
down_revision: str | None = "0034_shadow_mtf_adx_by_tf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    is_pg = dialect.startswith("postgres")
    json_type = "JSONB" if is_pg else "TEXT"
    id_type = "BIGSERIAL" if is_pg else "INTEGER"
    ts_type = "TIMESTAMPTZ" if is_pg else "TEXT"

    op.execute(f"""
        CREATE TABLE dispatch_decisions (
            id {id_type} PRIMARY KEY {'' if is_pg else 'AUTOINCREMENT'},
            symbol VARCHAR(20) NOT NULL,
            timeframe VARCHAR(8) NOT NULL,
            ts {ts_type} NOT NULL,
            direction VARCHAR(8),
            final_score DOUBLE PRECISION,
            gate_verdicts {json_type} NOT NULL,
            outcome VARCHAR(64) NOT NULL,
            detail TEXT,
            created_at {ts_type} NOT NULL {"DEFAULT now()" if is_pg else "DEFAULT CURRENT_TIMESTAMP"}
        );
    """)
    op.execute(
        "CREATE INDEX dispatch_decisions_symbol_ts_idx "
        "ON dispatch_decisions (symbol, ts);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS dispatch_decisions_symbol_ts_idx;")
    op.execute("DROP TABLE IF EXISTS dispatch_decisions;")
