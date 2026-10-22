"""liquidity_severity_events -- durable record of every fast-exit severity firing

The #514 fast-exit severity trigger was observable ONLY in logs. Verified
2026-10-22: the severe condition had been met 76 times in production
without any alarm, because the check sat on one of the three paths by
which a severely illiquid symbol can be retained. Fixing the two
uncovered paths (PR #601) makes the alarm fire -- into finite log
retention, which this project has now lost instrumentation to three
times (FU-47, the entry-timing recon's eight days, #447's detector
streaks).

At roughly 29 events per 30 days this table costs a few rows a month and
turns "has this happened, and when" from a grep race into a query.

Each row is SELF-DESCRIBING: the floor values and severity ratios in
force at the time are stored alongside the measurement, so a later
threshold change cannot silently reinterpret history. The ERROR logs are
kept as well -- they are for a human reading live; this is for the
question asked afterwards.

Deliberately NOT hash-chained. `live_fleet_universe` is not either, and
chaining would expose this to the FU-24 concurrent-insert race for no
benefit on an append-only observability record.

Revision ID: 0043_liq_severity_events
Revises: 0042_symsrc_null
Create Date: 2026-10-22
"""
from collections.abc import Sequence

from alembic import op


revision: str = "0043_liq_severity_events"
down_revision: str | None = "0042_symsrc_null"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    is_pg = dialect.startswith("postgres")
    ts_type = "TIMESTAMPTZ" if is_pg else "TEXT"
    pk = (
        "id BIGSERIAL PRIMARY KEY" if is_pg
        else "id INTEGER PRIMARY KEY AUTOINCREMENT"
    )

    op.execute(f"""
        CREATE TABLE liquidity_severity_events (
            {pk},
            occurred_at {ts_type} NOT NULL,
            symbol VARCHAR(20) NOT NULL,
            path TEXT NOT NULL,
            cohort TEXT,
            depth_0_5pct_usdt DOUBLE PRECISION NOT NULL,
            spread_bps DOUBLE PRECISION NOT NULL,
            depth_floor_usdt DOUBLE PRECISION NOT NULL,
            spread_max_bps DOUBLE PRECISION NOT NULL,
            severe_depth_ratio DOUBLE PRECISION NOT NULL,
            severe_spread_ratio DOUBLE PRECISION NOT NULL,
            depth_is_severe BOOLEAN NOT NULL,
            spread_is_severe BOOLEAN NOT NULL,
            override_retained BOOLEAN NOT NULL
        );
    """)
    op.execute(
        "CREATE INDEX liquidity_severity_events_occurred_idx "
        "ON liquidity_severity_events (occurred_at DESC);"
    )
    op.execute(
        "CREATE INDEX liquidity_severity_events_symbol_idx "
        "ON liquidity_severity_events (symbol, occurred_at DESC);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS liquidity_severity_events_symbol_idx;")
    op.execute("DROP INDEX IF EXISTS liquidity_severity_events_occurred_idx;")
    op.execute("DROP TABLE IF EXISTS liquidity_severity_events;")
