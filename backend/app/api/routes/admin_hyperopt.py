"""Admin REST endpoints for the hyperopt framework (SP-7 Phase C4).

POST /api/v1/admin/hyperopt        - kick off a hyperopt study; return a
202 ACCEPTED with a 'running' row immediately and dispatch the work to
``BackgroundTasks``.

GET  /api/v1/admin/hyperopt        - list recent studies, newest-first.
GET  /api/v1/admin/hyperopt/{id}   - poll a single study.

All admin-gated via ``Depends(require_admin)`` per spec section 6.4.

Hyperopt is much slower than backtest (n_trials x 2 backtests each is
typically 5-30 minutes for 20 trials) so we never block the request:
the operator polls the GET endpoint until ``status != 'running'``.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.deps import require_admin
from app.auth.models import User
from app.db.session import get_session, get_session_factory
from tools.hyperopt import hyperopt_layer_weights


def get_background_session_factory() -> async_sessionmaker[AsyncSession]:
    """Indirection so tests can override the background-task session factory.

    The request-scoped ``get_session`` dep is overridden in tests, but
    ``BackgroundTasks`` runs after the response and needs its own factory.
    Tests override this dep to inject the in-memory SQLite factory.
    """
    return get_session_factory()

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/admin/hyperopt",
    tags=["admin-hyperopt"],
    dependencies=[Depends(require_admin)],
)

# Default n_trials cap — protects against accidental runaway studies.
_MAX_N_TRIALS: int = 500


class HyperoptRunIn(BaseModel):
    symbol: str = "BTC/USDT"
    timeframe: str = "1h"
    train_start: datetime
    train_end: datetime
    val_start: datetime
    val_end: datetime
    n_trials: int = 20
    seed: int = 42


class HyperoptOut(BaseModel):
    id: int
    symbol: str
    timeframe: str
    n_trials: int
    best_weights: dict[str, float] | None
    best_sharpe: float | None
    status: str
    triggered_at: datetime
    completed_at: datetime | None
    error_message: str | None


def _coerce_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _maybe_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    return _coerce_dt(value)


def _maybe_json(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def _row_to_out(row: Any) -> HyperoptOut:
    return HyperoptOut(
        id=row.id, symbol=row.symbol, timeframe=row.timeframe,
        n_trials=row.n_trials,
        best_weights=_maybe_json(row.best_weights),
        best_sharpe=row.best_sharpe,
        status=row.status,
        triggered_at=_coerce_dt(row.triggered_at),
        completed_at=_maybe_dt(row.completed_at),
        error_message=row.error_message,
    )


async def _run_hyperopt_in_background(
    study_id: int,
    body: HyperoptRunIn,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """Run Optuna in a thread; update the study row when done.

    Caller passes the session factory explicitly so the background task
    is decoupled from the request session lifecycle.
    """
    try:
        result = await asyncio.to_thread(
            hyperopt_layer_weights,
            symbol=body.symbol, timeframe=body.timeframe,
            train_window=(body.train_start, body.train_end),
            val_window=(body.val_start, body.val_end),
            n_trials=body.n_trials, seed=body.seed,
        )
        async with factory() as session:
            await session.execute(sa.text(
                "UPDATE hyperopt_studies SET best_weights = :bw, "
                "best_sharpe = :bs, status = 'completed', "
                "completed_at = :ca WHERE id = :i"
            ), {
                "bw": json.dumps(
                    {str(k): v for k, v in result.best_weights.items()}
                ),
                "bs": result.best_sharpe,
                "ca": datetime.now(timezone.utc),
                "i": study_id,
            })
            await session.commit()
    except Exception as e:  # noqa: BLE001 — best-effort background task
        log.exception("hyperopt background task failed for id=%s", study_id)
        async with factory() as session:
            await session.execute(sa.text(
                "UPDATE hyperopt_studies SET status='failed', "
                "error_message=:em, completed_at=:ca WHERE id=:i"
            ), {
                "em": str(e)[:500],
                "ca": datetime.now(timezone.utc),
                "i": study_id,
            })
            await session.commit()


@router.post(
    "",
    response_model=HyperoptOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_hyperopt_endpoint(
    body: HyperoptRunIn,
    bg: BackgroundTasks,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    user: User = Depends(require_admin),  # noqa: B008
    bg_factory: async_sessionmaker[AsyncSession] = Depends(  # noqa: B008
        get_background_session_factory,
    ),
) -> HyperoptOut:
    """Kick off a hyperopt study. Returns 202 with a 'running' row.

    The actual Optuna optimization runs in ``BackgroundTasks`` and updates
    the row to 'completed' (or 'failed') when done. Operators poll
    ``GET /api/v1/admin/hyperopt/{id}`` to observe progress.
    """
    if body.n_trials > _MAX_N_TRIALS:
        raise HTTPException(
            status_code=400,
            detail=f"n_trials capped at {_MAX_N_TRIALS}",
        )
    if body.n_trials < 1:
        raise HTTPException(status_code=400, detail="n_trials must be >= 1")

    train_range = (
        f"[{body.train_start.isoformat()},{body.train_end.isoformat()})"
    )
    val_range = (
        f"[{body.val_start.isoformat()},{body.val_end.isoformat()})"
    )
    insert_sql = sa.text(
        "INSERT INTO hyperopt_studies "
        "(triggered_by, n_trials, train_window, val_window, symbol, "
        "timeframe, status) "
        "VALUES (:tb, :n, :tw, :vw, :sym, :tf, 'running')"
    )
    await session.execute(insert_sql, {
        "tb": user.id, "n": body.n_trials, "tw": train_range, "vw": val_range,
        "sym": body.symbol, "tf": body.timeframe,
    })
    # Read the new id back without RETURNING — works on SQLite + Postgres.
    row = (await session.execute(sa.text(
        "SELECT id FROM hyperopt_studies "
        "WHERE triggered_by = :tb AND symbol = :sym AND timeframe = :tf "
        "AND status = 'running' "
        "ORDER BY id DESC LIMIT 1"
    ), {
        "tb": user.id, "sym": body.symbol, "tf": body.timeframe,
    })).first()
    if row is None:  # pragma: no cover — should never happen post-INSERT
        raise HTTPException(
            status_code=500, detail="row not visible after insert",
        )
    new_id = int(row.id)
    await session.commit()

    bg.add_task(_run_hyperopt_in_background, new_id, body, bg_factory)

    full = (await session.execute(sa.text(
        "SELECT * FROM hyperopt_studies WHERE id = :i"
    ), {"i": new_id})).first()
    if full is None:  # pragma: no cover
        raise HTTPException(
            status_code=500, detail="row not visible after insert",
        )
    return _row_to_out(full)


@router.get("", response_model=list[HyperoptOut])
async def list_hyperopt_studies(
    symbol: str | None = Query(None),
    timeframe: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=500),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[HyperoptOut]:
    """List recent hyperopt studies, newest first.

    Supports optional ``symbol``, ``timeframe``, and ``status`` filters.
    """
    where: list[str] = []
    params: dict[str, Any] = {"l": limit}
    if symbol:
        where.append("symbol = :sym")
        params["sym"] = symbol
    if timeframe:
        where.append("timeframe = :tf")
        params["tf"] = timeframe
    if status_filter:
        where.append("status = :st")
        params["st"] = status_filter
    where_clause = ("WHERE " + " AND ".join(where)) if where else ""
    rows = (await session.execute(sa.text(
        f"SELECT * FROM hyperopt_studies {where_clause} "
        "ORDER BY triggered_at DESC, id DESC LIMIT :l"
    ), params)).all()
    return [_row_to_out(r) for r in rows]


@router.get("/{study_id}", response_model=HyperoptOut)
async def get_hyperopt_study(
    study_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> HyperoptOut:
    """Poll a single hyperopt study by id."""
    row = (await session.execute(sa.text(
        "SELECT * FROM hyperopt_studies WHERE id = :i"
    ), {"i": study_id})).first()
    if row is None:
        raise HTTPException(
            status_code=404, detail="hyperopt study not found",
        )
    return _row_to_out(row)
