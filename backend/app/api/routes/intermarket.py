"""SP-3.5 Phase E2: GET /api/v1/intermarket/{symbol}."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.schemas import IntermarketSnapshotOut
from app.auth.deps import current_user_or_impersonated
from app.auth.models import User
from app.data.adapters import get_adapter, get_intermarket_adapter  # noqa: F401
from app.data.intermarket_correlations import compute_30d_correlations
from app.data.intermarket_persistence import (
    latest_snapshot_for,
    snapshot_at_or_before,
)
from app.db.session import get_session
from datetime import timedelta


router = APIRouter(prefix="/api/v1", tags=["intermarket"])


@router.get("/intermarket/{symbol:path}", response_model=IntermarketSnapshotOut)
async def get_intermarket_snapshot(
    symbol: str,
    user: User = Depends(current_user_or_impersonated),
    session=Depends(get_session),
) -> IntermarketSnapshotOut:
    latest = await latest_snapshot_for(session, symbol)
    if latest is None:
        raise HTTPException(status_code=404, detail="no snapshot for symbol")
    baseline = await snapshot_at_or_before(
        session, symbol, ts=latest.captured_at - timedelta(hours=24),
    )
    oi_delta_pct: float | None = None
    if (latest.open_interest is not None and baseline is not None
            and baseline.open_interest is not None
            and baseline.open_interest > 0):
        oi_delta_pct = (latest.open_interest - baseline.open_interest) / baseline.open_interest

    binance = get_adapter("binance")
    yahoo = get_adapter("yahoo")
    dxy_corr, gold_corr = await compute_30d_correlations(
        symbol, binance_adapter=binance, yahoo_adapter=yahoo,
    )

    return IntermarketSnapshotOut(
        symbol=symbol,
        funding_rate=latest.funding_rate,
        mark_price=latest.mark_price,
        open_interest=latest.open_interest,
        open_interest_delta_24h_pct=oi_delta_pct,
        dxy_correlation_30d=dxy_corr,
        gold_correlation_30d=gold_corr,
        captured_at=latest.captured_at,
    )
