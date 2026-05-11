"""Public prediction telemetry endpoint (Feature 2).

Surfaces the rolling accuracy of recent live predictions for the
operator's chart UI. Per-user (uses ``current_user_or_impersonated``
+ filters on the validation rows' ``user_id`` column).
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user_or_impersonated
from app.auth.models import User
from app.db.session import get_session

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/predictions", tags=["predictions"])


class DirectionAccuracy(BaseModel):
    n: int
    correct: int
    accuracy_pct: float


class PredictionAccuracyOut(BaseModel):
    symbol: str
    timeframe: str
    validated_count: int
    correct_count: int
    accuracy_pct: float
    pending_count: int
    by_direction: dict[str, DirectionAccuracy]
    avg_pnl_pct: float | None
    last_validated_at: str | None


def _normalize_pair(symbol_path: str) -> str:
    return symbol_path.replace("-", "/").upper()


@router.get("/accuracy/{symbol_path}/{timeframe}", response_model=PredictionAccuracyOut)
async def accuracy(
    symbol_path: str,
    timeframe: str,
    window: int = Query(default=100, ge=10, le=1000),
    current_user: User = Depends(current_user_or_impersonated),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> PredictionAccuracyOut:
    """Rolling accuracy over the last ``window`` validated predictions."""
    pair = _normalize_pair(symbol_path)
    rows = (
        await session.execute(
            sa.text(
                "SELECT direction, was_correct, pnl_pct, validated_at "
                "FROM prediction_validations "
                "WHERE user_id = :uid AND symbol = :sym AND timeframe = :tf "
                "AND validated_at IS NOT NULL "
                "ORDER BY anchor_ts DESC LIMIT :lim"
            ),
            {
                "uid": current_user.id, "sym": pair,
                "tf": timeframe, "lim": window,
            },
        )
    ).all()

    pending = (
        await session.execute(
            sa.text(
                "SELECT count(*) AS n FROM prediction_validations "
                "WHERE user_id = :uid AND symbol = :sym AND timeframe = :tf "
                "AND validated_at IS NULL"
            ),
            {"uid": current_user.id, "sym": pair, "tf": timeframe},
        )
    ).first()
    pending_count = int(pending.n) if pending is not None else 0

    validated_count = len(rows)
    correct_count = sum(1 for r in rows if bool(r.was_correct))
    accuracy_pct = (correct_count / validated_count * 100.0) if validated_count else 0.0

    by_dir: dict[str, DirectionAccuracy] = {}
    for direction in ("LONG", "SHORT", "NEUTRAL"):
        subset = [r for r in rows if r.direction == direction]
        n = len(subset)
        correct = sum(1 for r in subset if bool(r.was_correct))
        by_dir[direction] = DirectionAccuracy(
            n=n, correct=correct,
            accuracy_pct=(correct / n * 100.0) if n else 0.0,
        )

    pnl_values = [float(r.pnl_pct) for r in rows if r.pnl_pct is not None]
    avg_pnl_pct = (sum(pnl_values) / len(pnl_values)) if pnl_values else None

    last_validated_at: str | None = None
    if rows:
        v = rows[0].validated_at
        last_validated_at = v.isoformat() if hasattr(v, "isoformat") else str(v) if v is not None else None

    return PredictionAccuracyOut(
        symbol=pair, timeframe=timeframe,
        validated_count=validated_count,
        correct_count=correct_count,
        accuracy_pct=round(accuracy_pct, 1),
        pending_count=pending_count,
        by_direction=by_dir,
        avg_pnl_pct=round(avg_pnl_pct, 3) if avg_pnl_pct is not None else None,
        last_validated_at=last_validated_at,
    )
