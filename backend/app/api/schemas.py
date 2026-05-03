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


class SignalMarkersOut(BaseModel):
    """Historical signal levels for chart overlay (chart deeplink target)."""
    signal_id: str
    direction: Literal["LONG", "SHORT"]
    entry: float
    stop_loss: float
    take_profit: float
    opened_at: datetime
    closed_at: datetime | None = None
    exit_price: float | None = None
    exit_reason: Literal["TAKE_PROFIT", "STOP_LOSS", "TIMEOUT"] | None = None


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
    signal_markers: SignalMarkersOut | None = None


# --- Phase J: Bot Status tab schemas ---


class WindowStatsOut(BaseModel):
    window: Literal["24h", "7d", "30d", "lifetime"]
    trades: int
    pnl_usdt: float
    pnl_pct: float | None
    win_rate: float
    sharpe_annualized: float | None
    max_drawdown: float
    profit_factor: float


class BotOverviewOut(BaseModel):
    last_24h: WindowStatsOut
    last_7d: WindowStatsOut
    last_30d: WindowStatsOut
    long_only_30d: WindowStatsOut
    short_only_30d: WindowStatsOut


class GateMetricOut(BaseModel):
    name: str
    current: float | None
    threshold: float
    operator: Literal[">=", "<="]
    passing: bool


class PromotionGateOut(BaseModel):
    target_mode: Literal["telegram-approve", "fully-auto"]
    metrics: list[GateMetricOut]
    all_passing: bool
    distance_summary: str


class OpenPositionOut(BaseModel):
    symbol: str
    direction: Literal["LONG", "SHORT"]
    entry_price: float
    stop_loss: float
    take_profit: float
    position_size_usdt: float
    bars_held: int
    opened_at: datetime
    signal_id: str
    current_price: float | None = None
    unrealized_pnl_pct: float | None = None
    unrealized_pnl_usdt: float | None = None


class PerAssetStatOut(BaseModel):
    symbol: str
    trades: int
    win_rate: float
    avg_rr: float
    pnl_usdt: float
    sharpe_annualized: float | None


class RecentTradeOut(BaseModel):
    closed_at: datetime
    symbol: str
    direction: Literal["LONG", "SHORT"]
    entry_price: float
    exit_price: float
    pnl_pct: float
    pnl_usdt: float
    exit_reason: Literal["TAKE_PROFIT", "STOP_LOSS", "TIMEOUT"]
    bars_held: int
    signal_id: str


class LongShortBreakdownOut(BaseModel):
    long: WindowStatsOut
    short: WindowStatsOut


class EquityCurvePoint(BaseModel):
    date: datetime
    cumulative_pnl_usdt: float


class EquityCurveOut(BaseModel):
    days: int
    points: list[EquityCurvePoint]


class AssetUniverseEntryOut(BaseModel):
    symbol: str
    rank: int
    quote_volume_24h_usdt: float


class AssetUniverseOut(BaseModel):
    snapshot_at: datetime
    entries: list[AssetUniverseEntryOut]
