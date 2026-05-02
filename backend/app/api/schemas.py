from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class LayerScoreOut(BaseModel):
    direction: Literal["LONG", "SHORT", "NEUTRAL"]
    strength: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    notes: str = ""


class FinalScoreOut(BaseModel):
    score: float = Field(ge=-1.0, le=1.0)
    direction: Literal["LONG", "SHORT", "NEUTRAL"]
    confidence: float = Field(ge=0.0, le=1.0)
    contributing_layers: list[int]


class TradeSetupOut(BaseModel):
    direction: Literal["LONG", "SHORT", "NEUTRAL"]
    entry: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    risk_reward: float | None = None


class MomentumPanelOut(BaseModel):
    rsi: float | None
    macd_line: float | None
    macd_signal: float | None
    macd_hist: float | None


class LivePredictionOut(BaseModel):
    symbol: str
    timeframe: str
    ts: datetime
    price: float
    final: FinalScoreOut
    layer_scores: dict[str, LayerScoreOut | None]
    trade_setup: TradeSetupOut
    momentum: MomentumPanelOut
    cold_start: bool = True
    inputs_hash: str
