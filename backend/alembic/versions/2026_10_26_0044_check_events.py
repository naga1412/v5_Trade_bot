"""check_events -- the retrieval path every guard, probe and counter was missing

THE PROBLEM THIS SOLVES IS NOT "we need more logging". It is that this
project has now shipped instrumentation with NO WAY TO READ IT BACK five
times, and each time the data was gone by the time the question arrived:

  1. FU-47 -- the entry-timing recon, eight days of work on a signal that
     was being recorded nowhere retrievable.
  2. #447's detector streaks -- counted in process memory, lost on every
     restart.
  3. #601 -- log-only, against a log buffer that docker truncates on each
     redeploy.
  4. #610's mtf fetch-failure counter -- a module-level dict, correct and
     useful and readable by absolutely nothing. It is the first customer
     of this table.
  5. `phase4-universe-discrepancy-check` -- worse than lost: it was a
     SHADOWED base64 script that never ran for months while every run
     exited 0. Nothing was recorded because nothing executed, and the
     absence was indistinguishable from success.

Case 5 is why this table is not just an events log. SILENCE IS NOT
SUCCESS: a check that should have run and left no row is itself the
finding, so the detector in `app/ops/check_events.py` iterates a declared
REGISTRY and left-joins this table, rather than grouping over whatever
rows happen to exist. Grouping over the table can only ever report checks
that already reported -- it cannot report the silent ones, which are the
dangerous ones.

GRANULARITY IS MATCHED TO THE ALARM, NOT TO THE EVENT. Two modes:

  * granularity='event' (bucket_date NULL) -- one row per occurrence, for
    SPARSE alarms. `liquidity_severity_events` sits at ~29 rows/30 days
    and that table KEEPS its own dedicated shape; this is for the general
    case.
  * granularity='daily' (bucket_date set) -- one row per
    (check_name, subject, day) whose `observed_count` is incremented.
    For CONTINUOUS failures: #610's mtf fetch failure is 56 symbols times
    the scoring cadence, and writing that per event would be write
    amplification wearing an instrumentation costume.

The unique index is UNCONDITIONAL over (check_name, subject, bucket_date)
rather than partial. Both Postgres and SQLite treat NULLs as DISTINCT in a
unique index, so 'event' rows (bucket_date NULL) never collide with each
other and need no predicate, while 'daily' rows collapse exactly as
intended. `subject` is therefore NOT NULL DEFAULT '' -- a NULL subject
would silently defeat daily collapsing by making every row distinct.

NO AUTO-REMEDIATION IS BUILT ON THIS TABLE, by decision and not by
omission. `register_brain.py:205` was an auto-healer -- "no active
champion = resolve it automatically" -- and it installed untested brains
for 64 nights before its own verification probe activated one in prod.
#571's fix was not a better healer; it was refusing to heal by default.
This table DETECTS. A human decides.

Deliberately NOT hash-chained, following `liquidity_severity_events` and
`live_fleet_universe`: chaining would expose an append-only observability
record to the FU-24 concurrent-insert race for no benefit.

Revision ID: 0044_check_events
Revises: 0043_liq_severity_events
Create Date: 2026-10-26
"""
from collections.abc import Sequence

from alembic import op


revision: str = "0044_check_events"
down_revision: str | None = "0043_liq_severity_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    is_pg = dialect.startswith("postgres")
    ts_type = "TIMESTAMPTZ" if is_pg else "TEXT"
    date_type = "DATE" if is_pg else "TEXT"
    json_type = "JSONB" if is_pg else "TEXT"
    pk = (
        "id BIGSERIAL PRIMARY KEY" if is_pg
        else "id INTEGER PRIMARY KEY AUTOINCREMENT"
    )

    op.execute(f"""
        CREATE TABLE check_events (
            {pk},
            check_name TEXT NOT NULL,
            check_kind TEXT NOT NULL,
            status TEXT NOT NULL,
            granularity TEXT NOT NULL,
            bucket_date {date_type},
            subject TEXT NOT NULL DEFAULT '',
            observed_count INTEGER NOT NULL DEFAULT 1,
            detail {json_type},
            first_seen_at {ts_type} NOT NULL,
            last_seen_at {ts_type} NOT NULL
        );
    """)

    # Daily rows collapse on this; event rows carry bucket_date NULL and
    # NULLs are distinct in a unique index on both dialects, so they are
    # unconstrained. See the module docstring -- this is load-bearing.
    op.execute(
        "CREATE UNIQUE INDEX check_events_daily_uq "
        "ON check_events (check_name, subject, bucket_date);"
    )
    # The silence detector's access path: newest row per check_name.
    op.execute(
        "CREATE INDEX check_events_name_seen_idx "
        "ON check_events (check_name, last_seen_at DESC);"
    )
    op.execute(
        "CREATE INDEX check_events_seen_idx "
        "ON check_events (last_seen_at DESC);"
    )
    # Status-first path for "what is currently violating", which is a
    # different question from "what reported recently".
    op.execute(
        "CREATE INDEX check_events_status_idx "
        "ON check_events (status, last_seen_at DESC);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS check_events_status_idx;")
    op.execute("DROP INDEX IF EXISTS check_events_seen_idx;")
    op.execute("DROP INDEX IF EXISTS check_events_name_seen_idx;")
    op.execute("DROP INDEX IF EXISTS check_events_daily_uq;")
    op.execute("DROP TABLE IF EXISTS check_events;")
