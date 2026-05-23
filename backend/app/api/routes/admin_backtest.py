"""Admin REST endpoints for the backtest framework (SP-7 Phase B5).

POST /api/v1/admin/backtests   - kick off a backtest synchronously, persist
the result, return the row.
GET  /api/v1/admin/backtests   - list recent backtests with optional filters.

Both behind ``Depends(require_admin)`` per spec section 6.4.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_admin
from app.auth.models import User  # noqa: F401 — kept for type-only annotation
from app.db.session import get_session

# PR-AUDIT-FIXES-1 (2026-05-23): the original module-level import
#   `from tools.backtest import persist_backtest_result, run_backtest`
# referenced a `tools.backtest` module that does NOT EXIST in the codebase.
# Importing this module crashed the FastAPI worker at startup AND any call to
# POST /api/v1/admin/backtests returned 500 (ModuleNotFoundError). PR-FULL-
# SYSTEM-AUDIT (Cat A3.1) identified this as a critical dead-code finding,
# same class as the `_evaluate_sharpe` ghost-import fixed by PR-BRAIN-
# BACKTEST-PHASEB5 in champion_challenger.py.
#
# Resolution: drop the broken module-level import (which crashed module
# load) and replace the POST handler with a 501 NOT IMPLEMENTED stub.
# The GET handler stays — it reads the existing `backtests` table via raw
# SQL and never needed tools.backtest.
#
# If/when a real backtest harness for the SP-7 ConvLSTM path is built, the
# POST handler should be re-implemented to drive that harness. For now, no
# operator was calling it (per PR-BACKTEST-1's findings + this PR's audit),
# so the 501 is honest behavior.

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
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
)
async def run_backtest_endpoint(
    body: BacktestRunIn,
) -> dict[str, str]:
    """STUB — backtest harness not yet implemented for this path.

    Previously raised 500 due to the missing ``tools.backtest`` module
    (see file-header comment). Now returns 501 with a clear message so
    callers know the path is intentionally disabled, not silently broken.

    GET /api/v1/admin/backtests still works (lists existing rows).
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=(
            "Backtest harness for the SP-7 ConvLSTM path is not "
            "implemented. The `tools.backtest` module referenced by this "
            "endpoint does not exist; the endpoint returns 501 instead "
            "of crashing with ModuleNotFoundError. Operator can list "
            "existing backtests via GET /api/v1/admin/backtests."
        ),
    )


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
