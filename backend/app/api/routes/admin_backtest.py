"""Admin REST endpoints for the backtest framework (SP-7 Phase B5).

POST /api/v1/admin/backtests   - kick off a backtest synchronously, persist
the result, return the row.
GET  /api/v1/admin/backtests   - list recent backtests with optional filters.

Both behind ``Depends(require_admin)`` per spec section 6.4.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_admin
from app.auth.models import User
from app.db.session import get_session
from tools.backtest import persist_backtest_result, run_backtest

router = APIRouter(
    prefix="/api/v1/admin/backtests",
    tags=["admin-backtest"],
    dependencies=[Depends(require_admin)],
)


class BacktestRunIn(BaseModel):
    symbol: str = "BTC/USDT"
    timeframe: str = "1h"
    start: datetime
    end: datetime
    layer_weights: dict[str, float] | None = None  # JSON keys are strings
    enabled_layers: list[int] | None = None
    enabled_traps: list[str] | None = None
    initial_balance_usdt: float = 10000.0


class BacktestOut(BaseModel):
    id: int
    symbol: str
    timeframe: str
    start_ts: datetime
    end_ts: datetime
    n_trades: int
    win_rate: float | None
    profit_factor: float | None
    sharpe: float | None
    max_drawdown: float | None
    params_hash: str
    status: str
    triggered_at: datetime
    layer_weights: dict[str, float] | None
    enabled_layers: list[int] | None
    enabled_traps: list[str] | None


def _coerce_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _maybe_json(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def _row_to_out(row: Any) -> BacktestOut:
    return BacktestOut(
        id=row.id,
        symbol=row.symbol, timeframe=row.timeframe,
        start_ts=_coerce_dt(row.start_ts),
        end_ts=_coerce_dt(row.end_ts),
        n_trades=row.n_trades, win_rate=row.win_rate,
        profit_factor=row.profit_factor, sharpe=row.sharpe,
        max_drawdown=row.max_drawdown,
        params_hash=row.params_hash, status=row.status,
        triggered_at=_coerce_dt(row.triggered_at),
        layer_weights=_maybe_json(row.layer_weights),
        enabled_layers=_maybe_json(row.enabled_layers),
        enabled_traps=_maybe_json(row.enabled_traps),
    )


@router.post(
    "",
    response_model=BacktestOut,
    status_code=status.HTTP_201_CREATED,
)
async def run_backtest_endpoint(
    body: BacktestRunIn,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    user: User = Depends(require_admin),  # noqa: B008
) -> BacktestOut:
    """Kick off a backtest. Blocks until complete (typically 30-60s).

    Heavy numpy/pandas work runs inside ``asyncio.to_thread`` so the
    FastAPI event loop isn't blocked. v2 may push to a background queue.
    """
    layer_weights_int = (
        {int(k): v for k, v in body.layer_weights.items()}
        if body.layer_weights else None
    )
    enabled_layers = set(body.enabled_layers) if body.enabled_layers else None
    enabled_traps = set(body.enabled_traps) if body.enabled_traps else None

    result = await asyncio.to_thread(
        run_backtest,
        symbol=body.symbol,
        timeframe=body.timeframe,
        start=body.start, end=body.end,
        layer_weights=layer_weights_int,
        enabled_layers=enabled_layers,
        enabled_traps=enabled_traps,
        initial_balance_usdt=body.initial_balance_usdt,
    )
    new_id = await persist_backtest_result(
        session, result=result, triggered_by_user_id=user.id,
    )
    await session.commit()
    row = (await session.execute(sa.text(
        "SELECT * FROM backtests WHERE id = :i"
    ), {"i": new_id})).first()
    if row is None:  # pragma: no cover
        raise HTTPException(
            status_code=500, detail="row not visible after insert",
        )
    return _row_to_out(row)


@router.get("", response_model=list[BacktestOut])
async def list_backtests(
    symbol: str | None = Query(None),
    timeframe: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[BacktestOut]:
    """List recent backtests, newest first. Optional symbol/timeframe filters."""
    where: list[str] = []
    params: dict[str, Any] = {"l": limit}
    if symbol:
        where.append("symbol = :sym")
        params["sym"] = symbol
    if timeframe:
        where.append("timeframe = :tf")
        params["tf"] = timeframe
    where_clause = ("WHERE " + " AND ".join(where)) if where else ""
    rows = (await session.execute(sa.text(
        f"SELECT * FROM backtests {where_clause} "
        "ORDER BY triggered_at DESC, id DESC LIMIT :l"
    ), params)).all()
    return [_row_to_out(r) for r in rows]
