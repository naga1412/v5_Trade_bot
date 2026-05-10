import logging
from datetime import datetime, timezone

import httpx
import pandas as pd
import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import GhostOut, LivePredictionOut, SignalMarkersOut
from app.auth.deps import current_user_or_impersonated
from app.auth.models import User
from app.core.dataquality.validator import Candle
from app.core.predictor import build_prediction
from app.data.adapters.binance import BinanceClient, BinanceFuturesAdapter
from app.data.universe import is_tradable
from app.db.session import get_session
from app.ml.checkpoints import get_active_model_and_checkpoint
from app.ml.inference import predict_ghost_candle

_log = logging.getLogger(__name__)

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


async def _fetch_recent_candles(
    symbol: str, timeframe: str, *, limit: int = 500,
) -> list[Candle]:
    """Fetch klines, trying Binance Spot first then Futures fallback.

    Many Binance Futures perpetuals don't exist on Spot — most notably
    the ``1000XXX`` family (1000SHIB, 1000PEPE, 1000FLOKI, etc.) which
    Binance created so the price stays > 1 satoshi for low-priced
    coins. The user reported HTTP 500 on /predict/1000SHIB-USDT/1h
    — root cause was Spot returning 400, the unhandled HTTPStatusError
    surfaced as 500. We now silently fall back to Futures klines.
    """
    native = _to_binance_symbol(symbol)
    async with httpx.AsyncClient() as http:
        # 1. Try Spot.
        try:
            client = BinanceClient(http=http)
            bars = await client.fetch_klines(native, timeframe, limit=limit)
            if bars:
                return [
                    Candle(
                        symbol=symbol, timeframe=timeframe,
                        ts=b.ts, open=b.open, high=b.high,
                        low=b.low, close=b.close, volume=b.volume,
                    )
                    for b in bars
                ]
        except (httpx.HTTPStatusError, httpx.HTTPError) as e:
            _log.info(
                "spot fetch_klines failed for %s (%s); trying futures",
                native, e,
            )

        # 2. Fall back to Futures perpetual.
        try:
            futures = BinanceFuturesAdapter(http=http)
            bars = await futures.fetch_klines(native, timeframe, limit=limit)
        except (httpx.HTTPStatusError, httpx.HTTPError) as e:
            _log.warning(
                "futures fetch_klines also failed for %s: %s", native, e,
            )
            bars = []

    return [
        Candle(
            symbol=symbol, timeframe=timeframe,
            ts=b.ts, open=b.open, high=b.high,
            low=b.low, close=b.close, volume=b.volume,
        )
        for b in bars
    ]


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
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[CandleOut]:
    pair = _normalize_pair(symbol_path)
    if timeframe not in _TIMEFRAMES:
        raise HTTPException(400, f"Unsupported timeframe {timeframe}")
    if not await is_tradable(session, pair, datetime.now(timezone.utc)):
        raise HTTPException(404, f"Unknown symbol {pair}")
    cs = await _fetch_recent_candles(pair, timeframe, limit=min(1000, limit))
    return [
        CandleOut(time=int(c.ts.timestamp()), open=c.open, high=c.high,
                  low=c.low, close=c.close)
        for c in cs
    ]


async def _load_signal_markers(
    session: AsyncSession, *, user_id: int, signal_id: str,
) -> SignalMarkersOut | None:
    """Look up a closed shadow_trade by signal_id and return its chart markers.

    Spec §7.3: queries against shadow_trades MUST filter by user_id so a
    deeplink containing another user's signal_id 404s rather than leaking
    chart markers across accounts.
    """
    sql = (
        "SELECT signal_id, direction, entry_price, stop_loss, take_profit, "
        "opened_at, closed_at, exit_price, exit_reason "
        "FROM shadow_trades WHERE user_id = :uid AND signal_id = :sig LIMIT 1"
    )
    result = await session.execute(sa.text(sql), {"uid": user_id, "sig": signal_id})
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
    if not await is_tradable(session, pair, datetime.now(timezone.utc)):
        raise HTTPException(404, f"Unknown symbol {pair}")

    markers: SignalMarkersOut | None = None
    if signal is not None:
        markers = await _load_signal_markers(
            session, user_id=current_user.id, signal_id=signal,
        )
        if markers is None:
            raise HTTPException(404, f"Signal {signal} not found")

    candles = await _fetch_recent_candles(pair, timeframe, limit=300)
    if len(candles) < 200:
        raise HTTPException(503, "Insufficient candles to compute prediction")
    bars = _candles_to_df(candles)
    pred = await build_prediction(
        symbol=pair, timeframe=timeframe, bars=bars, session=session,
    )

    # SP-1: ghost candle for the REST initial-load case. The WS worker
    # attaches this on every closed-candle push; the REST endpoint did
    # not, which left the dashboard stuck on "no model" until the next
    # WS push arrived (potentially up to one timeframe interval after
    # page load). Mirror the WS worker's logic so initial paint is correct.
    active = get_active_model_and_checkpoint()
    if active is not None and len(bars) >= 256:
        model, _ck = active
        try:
            ghost = predict_ghost_candle(
                model=model,
                bars=bars,
                last_close=float(bars["close"].iloc[-1]),
            )
            pred = pred.model_copy(update={"ghost": GhostOut(
                open=ghost.open, high=ghost.high, low=ghost.low,
                close=ghost.close, p5_low=ghost.p5_low,
                p95_high=ghost.p95_high, uncertainty=ghost.uncertainty,
            )})
        except Exception as e:  # noqa: BLE001
            _log.warning("predict_ghost_candle failed in REST predict: %s", e)

    if markers is not None:
        pred = pred.model_copy(update={"signal_markers": markers})
    return pred
