"""PR-RL-DROP-L7 (2026-08-15): clear the stale is_active flag on rl_checkpoints.id=62.

rl_checkpoints.id=62 (model_name='ppo_policy_v1') is the DB-flagged-active
RL brain checkpoint, but it was trained under the OLD 58-dim observation
vector -- before the 2026-08-05 PR-OBS-DIM-REDUCE change dropped 4 dead
macro dims (58->54), and before this same PR's Part 1 (app/rl/obs.py)
drops the permanent L7 (XGBoost) stub from the layer-score tuple
(54->53). `model.load_state_dict()` in app/rl/checkpoints.py
(load_active_checkpoint) has been throwing a shape-mismatch RuntimeError
on every attempted load since the 58->54 change; it is caught, logged at
ERROR, and the loader returns None -- the intended fail-open contract
(compute_brain_adjust_and_persist() in app/rl/predictor_glue.py returns
brain_adjust=1.0, pure equal-weight, whenever no checkpoint is loaded).

Confirmed empirically: brain_decisions has written ZERO rows since
2026-08-05T12:30:09Z -- the brain has been silently neutral for 10 days.
Nobody was told, because the failure was a one-shot ERROR log at
container-boot time with no persistent/queryable/alerting surface. This
PR's Part 3 (backend/app/healer/detectors.py, C6_rl_checkpoint_load_health)
adds that surface going forward; this migration is the one-time, reviewed,
versioned fix for the stale flag itself -- direct DB writes are
operator-only in this project's workflow, so this cannot be an ad-hoc
UPDATE.

The `is_active = TRUE` guard in the WHERE clause makes this a safe no-op
if an operator has already deactivated id=62 (or activated a different,
compatible checkpoint) by the time this migration runs.

Downgrade is best-effort (re-sets is_active=TRUE unconditionally for
id=62) -- alembic downgrades are rare/operator-only in this project, so
this does not attempt to preserve whatever the pre-upgrade state actually
was for every edge case.

Revision ID: 0036_deactivate_rl_ckpt_62
Revises: 0035_dispatch_decisions
Create Date: 2026-08-15
"""
from collections.abc import Sequence

from alembic import op


revision: str = "0036_deactivate_rl_ckpt_62"
down_revision: str | None = "0035_dispatch_decisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Plain SQL, no dialect-specific syntax -- runs unchanged on both
    # Postgres and SQLite.
    op.execute(
        "UPDATE rl_checkpoints SET is_active = FALSE "
        "WHERE id = 62 AND model_name = 'ppo_policy_v1' AND is_active = TRUE;"
    )


def downgrade() -> None:
    # Best-effort revert only -- see module docstring.
    op.execute(
        "UPDATE rl_checkpoints SET is_active = TRUE "
        "WHERE id = 62 AND model_name = 'ppo_policy_v1';"
    )
