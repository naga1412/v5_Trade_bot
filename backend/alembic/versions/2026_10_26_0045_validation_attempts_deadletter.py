"""prediction_validations: attempt counter + terminal unvalidatable state

THE BUG THIS FIXES IS NOT THE BACKLOG. The backlog is a symptom and it is
already draining. The defect is that `validate_pending` selects

    WHERE validated_at IS NULL AND target_ts <= :now
    ORDER BY target_ts ASC LIMIT 100

-- oldest first -- while `_validate_one` returns False (a SILENT SKIP,
not an exception) whenever the close price cannot be fetched. A row that
can never be validated therefore stays NULL, stays FIRST, and is
re-selected on every pass forever. Once at least `batch_limit`
un-validatable rows accumulate at the head, the batch is entirely poison
and the queue stops. Nothing behind it is ever reached.

MEASURED, 2026-10-26: six symbols entered the stream at
2026-09-02 17:00Z -- 1000PEPE/USDT, 1000SHIB/USDT, HYPE/USDT,
USELESS/USDT, UAI/USDT, SKR/USDT -- and were validated in real time
ZERO times across 15-24 rows each. Their rows total **129**, against
`batch_limit=100`. Validation stopped on 2026-09-03 and stayed stopped
for 53 days. All six are tagged `futures_poll` from 2026-09-03 19:00Z
onward; they have no spot pair, and the validator's fetch was asking
Binance SPOT (the FU-53 / #539 wrong-endpoint class, which #609
migrated).

Raising `batch_limit` cannot fix this and is explicitly not done: the
blocking rows are by construction always first, so a larger batch
re-reads the same poison plus more. A drain that is 12-40x
over-provisioned and still not draining is not short of drain.

WHAT THE COLUMNS DO

  validation_attempts  -- incremented on every attempt that does NOT
                          produce a validation. The count alone makes a
                          skip observable; there was previously no
                          number anywhere distinguishing "nothing due"
                          from "the same 100 rows failed again".
  last_attempt_at      -- when that last attempt happened, so a stuck
                          row's age is readable without inferring it.
  unvalidatable_reason -- the TERMINAL state. Non-NULL means the row has
                          exhausted its attempts and is excluded from
                          selection, so it cannot block the rows behind
                          it. NULL means still eligible.

A dead-lettered row ends up unvalidated -- which is exactly what it was
already, except that today it also stops the queue. The difference is
that the outcome is now explicit, attributed and auditable rather than
an invisible stall. Re-opening a batch is a one-line UPDATE setting
`unvalidatable_reason = NULL`, deliberately left as an operator action.

NO BACKFILL. Existing rows take `validation_attempts = 0` and
`unvalidatable_reason = NULL`, so the current drain continues untouched
and nothing is retroactively written off.

THE PARTIAL INDEX matches the new selector exactly
(`validated_at IS NULL AND unvalidatable_reason IS NULL`), so the
hot query stays an index scan over the eligible set rather than a filter
over the whole table -- which matters at the ~157k rows this table
already holds.

Revision ID: 0045_validation_attempts
Revises: 0044_check_events
Create Date: 2026-10-26
"""
from collections.abc import Sequence

from alembic import op


revision: str = "0045_validation_attempts"
down_revision: str | None = "0044_check_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    is_pg = dialect.startswith("postgres")
    ts_type = "TIMESTAMPTZ" if is_pg else "TEXT"

    op.execute(
        "ALTER TABLE prediction_validations "
        "ADD COLUMN validation_attempts INTEGER NOT NULL DEFAULT 0"
    )
    op.execute(
        f"ALTER TABLE prediction_validations ADD COLUMN last_attempt_at {ts_type}"
    )
    op.execute(
        "ALTER TABLE prediction_validations ADD COLUMN unvalidatable_reason TEXT"
    )
    # Mirrors the selector's WHERE clause so the eligible set is an index
    # scan. Partial indexes are supported on both Postgres and the SQLite
    # test mirror.
    op.execute(
        "CREATE INDEX prediction_validations_eligible_idx "
        "ON prediction_validations (target_ts) "
        "WHERE validated_at IS NULL AND unvalidatable_reason IS NULL"
    )
    # The dead-letter review path: "what got written off, and why".
    op.execute(
        "CREATE INDEX prediction_validations_deadletter_idx "
        "ON prediction_validations (last_attempt_at) "
        "WHERE unvalidatable_reason IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS prediction_validations_deadletter_idx;")
    op.execute("DROP INDEX IF EXISTS prediction_validations_eligible_idx;")
    op.execute(
        "ALTER TABLE prediction_validations DROP COLUMN unvalidatable_reason"
    )
    op.execute("ALTER TABLE prediction_validations DROP COLUMN last_attempt_at")
    op.execute(
        "ALTER TABLE prediction_validations DROP COLUMN validation_attempts"
    )
