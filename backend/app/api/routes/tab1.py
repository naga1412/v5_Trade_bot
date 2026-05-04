from datetime import datetime, timezone

import httpx
import pandas as pd
import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import LivePredictionOut, SignalMarkersOut
from app.auth.deps import current_user_or_impersonated
from app.auth.models import User
from app.core.dataquality.validator import Candle
from app.core.predictor import build_prediction
from app.data.adapters.binance import BinanceClient
from app.data.universe import is_tradable
from app.db.session import get_session

router = APIRouter(prefix="/api/v1", tags=["tab1"])

_TIMEFRAMES = {"1m", "5m", "15m", "1h", "4h", "1d"}


class CandleOut(BaseModel):
    time: int  # unix seconds (lightweight-charts expects this)
    open: float
    high: float
    low: float
    close: float


def _normalize_pair(symbol_path: str) -> str:
    """BTC-USDT (URL-safe) -> BTC/USDT."""
    return symbol_path.replace("-", "/").upper()


def _to_binance_symbol(pair: str) -> str:
    return pair.replace("/", "")


async def _fetch_recent_candles(symbol: str, timeframe: str, *, limit: int = 500) -> list[Candle]:
    async with httpx.AsyncClient() as http:
        client = BinanceClient(http=http)
        return await client.fetch_klines(_to_binance_symbol(symbol), timeframe, limit=limit)


def _candles_to_df(candles: list[Candle]) -> pd.DataFrame:
    df = pd.DataFrame([c.__dict__ for c in candles])
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df.set_index("ts")[["open", "high", "low", "close", "volume"]]


@router.get("/candles/{symbol_path}/{timeframe}", response_model=list[CandleOut])
async def candles(
    symbol_path: str,
    timeframe: str,
    limit: int = 500,
    current_user: User = Depends(current_user_or_impersonated),  # noqa: B008
) -> list[CandleOut]:
    pair = _normalize_pair(symbol_path)
    if timeframe not in _TIMEFRAMES:
        raise HTTPException(400, f"Unsupported timeframe {timeframe}")
    if not is_tradable(pair, datetime.now(timezone.utc)):
        raise HTTPException(404, f"Unknown symbol {pair}")
    cs = await _fetch_recent_candles(pair, timeframe, limit=min(1000, limit))
    return [
        CandleOut(time=int(c.ts.timestamp()), open=c.open, high=c.high,
                  low=c.low, close=c.close)
        for c in cs
    ]


async def _load_signal_markers(
    session: AsyncSession, signal_id: str,
) -> SignalMarkersOut | None:
    """Look up a closed shadow_trade by signal_id and return its chart markers."""
    sql = (
        "SELECT signal_id, direction, entry_price, stop_loss, take_profit, "
        "opened_at, closed_at, exit_price, exit_reason "
        "FROM shadow_trades WHERE signal_id = :sig LIMIT 1"
    )
    result = await session.execute(sa.text(sql), {"sig": signal_id})
    row = result.first()
    if row is None:
        return None
    opened_at = row.opened_at
    if isinstance(opened_at, str):
        opened_at = datetime.fromisoformat(opened_at)
    closed_at = row.closed_at
    if isinstance(closed_at, str):
        closed_at = datetime.fromisoformat(closed_at)
    return SignalMarkersOut(
        signal_id=row.signal_id,
        direction=row.direction,  # type: ignore[arg-type]
        entry=row.entry_price,
        stop_loss=row.stop_loss,
        take_profit=row.take_profit,
        opened_at=opened_at,
        closed_at=closed_at,
        exit_price=row.exit_price,
        exit_reason=row.exit_reason,  # type: ignore[arg-type]
    )


@router.get("/predict/{symbol_path}/{timeframe}", response_model=LivePredictionOut)
async def predict(
    symbol_path: str,
    timeframe: str,
    signal: str | None = None,
    current_user: User = Depends(current_user_or_impersonated),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> LivePredictionOut:
    pair = _normalize_pair(symbol_path)
    if timeframe not in _TIMEFRAMES:
        raise HTTPException(400, f"Unsupported timeframe {timeframe}")
    if not is_tradable(pair, datetime.now(timezone.utc)):
        raise HTTPException(404, f"Unknown symbol {pair}")

    markers: SignalMarkersOut | None = None
    if signal is not None:
        markers = await _load_signal_markers(session, signal)
        if markers is None:
            raise HTTPException(404, f"Signal {signal} not found")

    candles = await _fetch_recent_candles(pair, timeframe, limit=300)
    if len(candles) < 200:
        raise HTTPException(503, "Insufficient candles to compute prediction")
    bars = _candles_to_df(candles)
    pred = build_prediction(symbol=pair, timeframe=timeframe, bars=bars)
    if markers is not None:
        pred = pred.model_copy(update={"signal_markers": markers})
    return pred
