from datetime import datetime, timezone

import httpx
import pandas as pd
from fastapi import APIRouter, HTTPException

from app.api.schemas import LivePredictionOut
from app.core.dataquality.validator import Candle
from app.core.predictor import build_prediction
from app.data.adapters.binance import BinanceClient
from app.data.universe import is_tradable

router = APIRouter(prefix="/api/v1", tags=["tab1"])

_TIMEFRAMES = {"1m", "5m", "15m", "1h", "4h", "1d"}


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


@router.get("/predict/{symbol_path}/{timeframe}", response_model=LivePredictionOut)
async def predict(symbol_path: str, timeframe: str) -> LivePredictionOut:
    pair = _normalize_pair(symbol_path)
    if timeframe not in _TIMEFRAMES:
        raise HTTPException(400, f"Unsupported timeframe {timeframe}")
    if not is_tradable(pair, datetime.now(timezone.utc)):
        raise HTTPException(404, f"Unknown symbol {pair}")

    candles = await _fetch_recent_candles(pair, timeframe, limit=300)
    if len(candles) < 200:
        raise HTTPException(503, "Insufficient candles to compute prediction")
    bars = _candles_to_df(candles)
    return build_prediction(symbol=pair, timeframe=timeframe, bars=bars)
