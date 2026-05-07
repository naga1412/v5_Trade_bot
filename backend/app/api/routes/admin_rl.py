"""Admin REST endpoints for the RL brain checkpoint registry (SP-4 Phase D).

Mirror of :mod:`app.api.routes.admin_ml` for the L10 PPO policy
checkpoints stored in the ``rl_checkpoints`` table (migration 0015).
The Colab/Hetzner trainer POSTs a fresh row at the end of every
successful nightly retrain; an admin then PATCHes ``is_active=true``
to promote it (which atomically deactivates the previous active row
for the same ``model_name``).

All routes are gated behind ``Depends(require_admin)`` from SP-0.7.
``DELETE`` is soft (sets ``is_active=false`` + ``deactivated_at=now``)
so the audit trail in ``brain_decisions.checkpoint_id`` keeps pointing
at deactivated rows.

Champion-challenger: SP-7's ``evaluate_challenger`` is reused with
``table="rl_checkpoints"`` + ``metric="sharpe"`` (HIGHER is better
for Sharpe, opposite of MAE). The first-checkpoint case still
auto-passes; ``?force=true`` always bypasses.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    RlCheckpointCreateIn,
    RlCheckpointOut,
    RlCheckpointPatchIn,
)
from app.auth.deps import require_admin
from app.db.session import get_session
from app.ml.champion_challenger import evaluate_challenger

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/admin/rl-checkpoints",
    tags=["admin-rl"],
    dependencies=[Depends(require_admin)],
)


def _coerce_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _row_to_out(row: Any) -> RlCheckpointOut:
    eval_results = row.eval_results
    if isinstance(eval_results, str):
        eval_results = json.loads(eval_results) if eval_results else {}
    return RlCheckpointOut(
        id=row.id,
        model_name=row.model_name,
        version=row.version,
        checkpoint_uri=row.checkpoint_uri,
        sha256=row.sha256,
        trained_at=_coerce_dt(row.trained_at) or datetime.now(timezone.utc),
        train_data_window=row.train_data_window,
        eval_results=eval_results or {},
        is_active=bool(row.is_active),
        activated_at=_coerce_dt(row.activated_at),
        deactivated_at=_coerce_dt(row.deactivated_at),
        notes=row.notes,
    )


_SELECT_COLS = (
    "id, model_name, version, checkpoint_uri, sha256, trained_at, "
    "train_data_window, eval_results, is_active, activated_at, "
    "deactivated_at, notes"
)


@router.post(
    "",
    response_model=RlCheckpointOut,
    status_code=status.HTTP_201_CREATED,
)
async def register_rl_checkpoint(
    body: RlCheckpointCreateIn,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> RlCheckpointOut:
    """Register a freshly-trained PPO policy. Always inactive on creation.

    Promotion to active is a separate explicit PATCH so the operator can
    review eval_results (Sharpe, max_dd, per-asset breakdown) before
    swapping the live brain.
    """
    insert_sql = sa.text(
        "INSERT INTO rl_checkpoints "
        "(model_name, version, checkpoint_uri, sha256, trained_at, "
        "train_data_window, eval_results, notes, is_active) "
        "VALUES (:n, :v, :u, :s, :t, :w, :e, :no, 0)"
    )
    await session.execute(insert_sql, {
        "n": body.model_name, "v": body.version, "u": body.checkpoint_uri,
        "s": body.sha256, "t": body.trained_at,
        "w": body.train_data_window, "e": json.dumps(body.eval_results),
        "no": body.notes,
    })
    row = (await session.execute(sa.text(
        f"SELECT {_SELECT_COLS} FROM rl_checkpoints "
        "WHERE model_name = :n AND version = :v"
    ), {"n": body.model_name, "v": body.version})).first()
    await session.commit()
    if row is None:  # pragma: no cover — should never happen post-INSERT
        raise HTTPException(status_code=500, detail="insert succeeded but row not visible")
    return _row_to_out(row)


@router.get("", response_model=list[RlCheckpointOut])
async def list_rl_checkpoints(
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[RlCheckpointOut]:
    """Return all RL checkpoints (active + historical), newest first."""
    rows = (await session.execute(sa.text(
        f"SELECT {_SELECT_COLS} FROM rl_checkpoints "
        "ORDER BY trained_at DESC, id DESC"
    ))).all()
    return [_row_to_out(r) for r in rows]


@router.patch("/{checkpoint_id}", response_model=RlCheckpointOut)
async def patch_rl_checkpoint(
    checkpoint_id: int,
    body: RlCheckpointPatchIn,
    force: bool = Query(
        False,
        description=(
            "Bypass the SP-7 champion-challenger gate (Sharpe variant). "
            "Used for the SP-4 first-checkpoint case (no champion to "
            "beat) and for emergency rollbacks. Logged at WARNING level "
            "for the audit trail."
        ),
    ),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> RlCheckpointOut:
    """Activate / deactivate an RL checkpoint and/or update operator notes.

    Activating row N atomically deactivates any currently-active row for
    the same ``model_name``. Champion-challenger gate uses
    ``metric="sharpe"`` — challenger wins iff
    ``challenger_sharpe > champion_sharpe * 1.05``.
    """
    existing = (await session.execute(
        sa.text("SELECT id, model_name FROM rl_checkpoints WHERE id = :i"),
        {"i": checkpoint_id},
    )).first()
    if existing is None:
        raise HTTPException(status_code=404, detail="checkpoint not found")

    now = datetime.now(timezone.utc)
    if body.is_active is True:
        if force:
            log.warning(
                "champion-challenger bypass (rl): admin force-activated "
                "checkpoint id=%s (model=%s)",
                checkpoint_id, existing.model_name,
            )
        else:
            cc = await evaluate_challenger(
                session,
                challenger_checkpoint_id=checkpoint_id,
                table="rl_checkpoints",
                metric="sharpe",
            )
            if not cc.challenger_wins:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "challenger did not beat champion: "
                        f"champion_sharpe={cc.champion_mae} "
                        f"challenger_sharpe={cc.challenger_mae} "
                        "(needs 5% improvement; pass ?force=true to bypass)"
                    ),
                )
        await session.execute(sa.text(
            "UPDATE rl_checkpoints SET is_active = FALSE, deactivated_at = :n "
            "WHERE model_name = :m AND is_active = TRUE AND id != :i"
        ), {"n": now, "m": existing.model_name, "i": checkpoint_id})
        await session.execute(sa.text(
            "UPDATE rl_checkpoints SET is_active = TRUE, activated_at = :n, "
            "deactivated_at = NULL WHERE id = :i"
        ), {"n": now, "i": checkpoint_id})
    elif body.is_active is False:
        await session.execute(sa.text(
            "UPDATE rl_checkpoints SET is_active = FALSE, deactivated_at = :n "
            "WHERE id = :i"
        ), {"n": now, "i": checkpoint_id})

    if body.notes is not None:
        await session.execute(sa.text(
            "UPDATE rl_checkpoints SET notes = :n WHERE id = :i"
        ), {"n": body.notes, "i": checkpoint_id})

    await session.commit()
    row = (await session.execute(sa.text(
        f"SELECT {_SELECT_COLS} FROM rl_checkpoints WHERE id = :i"
    ), {"i": checkpoint_id})).first()
    if row is None:  # pragma: no cover — race-only path
        raise HTTPException(status_code=404, detail="checkpoint not found")
    return _row_to_out(row)


@router.delete(
    "/{checkpoint_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_rl_checkpoint(
    checkpoint_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> Response:
    """Soft delete: mark inactive + record ``deactivated_at`` for audit.

    The row stays in the table so historical audit-chain queries on
    ``brain_decisions.checkpoint_id`` continue to resolve.
    """
    existing = (await session.execute(
        sa.text("SELECT id FROM rl_checkpoints WHERE id = :i"),
        {"i": checkpoint_id},
    )).first()
    if existing is None:
        raise HTTPException(status_code=404, detail="checkpoint not found")

    await session.execute(sa.text(
        "UPDATE rl_checkpoints SET is_active = FALSE, deactivated_at = :n "
        "WHERE id = :i"
    ), {"n": datetime.now(timezone.utc), "i": checkpoint_id})
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
