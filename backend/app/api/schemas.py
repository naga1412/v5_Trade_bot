from datetime import datetime
from typing import Any, Literal

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


class GhostOut(BaseModel):
    """Predicted next-bar ghost candle + uncertainty band (SP-1)."""
    open: float
    high: float
    low: float
    close: float
    p5_low: float
    p95_high: float
    uncertainty: float = Field(ge=0.0)


# --- SP-9 Phase F5: admin news REST schemas -------------------------------


class NewsArticleOut(BaseModel):
    """One ``news_items`` row exposed by ``GET /api/v1/admin/news``."""

    id: int
    source: str
    url: str
    title: str
    published_at: datetime
    fetched_at: datetime
    sentiment_score: float | None = None
    sentiment_label: Literal["positive", "negative", "neutral"] | None = None
    impact_score: float | None = None
    category: str | None = None
    affected_assets: list[str] = []


class NewsRefreshOut(BaseModel):
    """Result of ``POST /api/v1/admin/news/refresh`` — manual ingest trigger."""

    new_rows: int
    sources_polled: list[str]


class SentimentSummary(BaseModel):
    """SP-9 Phase F1: F&G index + L9 news bias summary for Tab 1.

    Backwards-compatible: surfaced on :class:`LivePredictionOut` only when
    the predictor is invoked with a session and L9 has data to summarise.
    """

    fng_value: int = Field(ge=0, le=100)
    fng_label: Literal[
        "Extreme Fear", "Fear", "Neutral", "Greed", "Extreme Greed",
    ]
    news_bias: Literal["Bullish", "Bearish", "Neutral"]


class NewsSummary(BaseModel):
    """SP-9 Phase F1: top recent-headline + impact aggregation for Tab 1."""

    recent_count: int = Field(ge=0)
    top_headline: str | None = None
    impact: Literal["LOW", "MEDIUM", "HIGH"]


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
    # SP-1: ghost candle prediction (None when no checkpoint loaded).
    ghost: GhostOut | None = None
    # SP-5 Phase F1: enriched payload (traps fired, raw scores, tier) the
    # persistence sites merge into ``predictions.layer_scores`` JSONB. Kept
    # off the strict ``layer_scores`` field so the API response shape stays
    # backwards compatible — frontend reads from ``layer_scores`` only.
    prediction_extras: dict[str, Any] | None = None
    # SP-9 Phase F1: optional summaries populated by predictor when a
    # session is passed AND the L9 news layer has data / F&G call succeeds.
    sentiment: SentimentSummary | None = None
    news: NewsSummary | None = None


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


# --- SP-0.7 Phase G: Admin schemas ----------------------------------------


class UserOut(BaseModel):
    id: int
    email: str
    display_name: str
    is_admin: bool
    is_active: bool
    trading_mode: str
    last_login: datetime | None = None
    created_at: datetime
    invited_by: int | None = None


class InvitationCreateIn(BaseModel):
    email: str
    display_name: str | None = None
    is_admin: bool = False
    notes: str | None = None


class InvitationOut(BaseModel):
    id: int
    email: str
    display_name: str | None = None
    invited_by: int
    invited_at: datetime
    accepted_at: datetime | None = None
    cf_access_added: bool


class UserPatchIn(BaseModel):
    is_active: bool | None = None
    is_admin: bool | None = None
    notes: str | None = None


class ImpersonationStartOut(BaseModel):
    admin_user_id: int
    target_user_id: int
    started_at: datetime


class AuditTrailEntry(BaseModel):
    table_name: str
    row_id: int
    user_id: int | None = None
    ts: datetime
    summary: str


# --- SP-0.7 Phase H: /me schemas ------------------------------------------


class MeOut(BaseModel):
    id: int
    email: str
    display_name: str
    is_admin: bool
    is_impersonating: bool
    trading_mode: str
    position_sizing_mode: str
    fixed_size_min_usdt: float | None = None
    fixed_size_max_usdt: float | None = None
    max_concurrent_positions: int | None = None
    max_leverage_cap: int | None = None
    quiet_hours_enabled: bool
    quiet_hours_start: str | None = None
    quiet_hours_end: str | None = None
    binance_keys_configured: bool
    telegram_configured: bool
    totp_configured: bool


class MePatchIn(BaseModel):
    display_name: str | None = None
    quiet_hours_enabled: bool | None = None
    quiet_hours_start: str | None = None
    quiet_hours_end: str | None = None
    fixed_size_min_usdt: float | None = None
    fixed_size_max_usdt: float | None = None
    max_concurrent_positions: int | None = None
    max_leverage_cap: int | None = None


class BinanceKeysIn(BaseModel):
    api_key: str
    api_secret: str


class TelegramIn(BaseModel):
    bot_token: str
    chat_id: str


class TotpSetupOut(BaseModel):
    provisioning_uri: str
    secret_for_display: str
    backup_codes: list[str]


class TotpVerifyIn(BaseModel):
    code: str


class TotpVerifyOut(BaseModel):
    ok: bool


class TradingModeChangeIn(BaseModel):
    """SP-8 Phase J — body for PATCH /me/trading-mode.

    `totp_code` is required when the change is an upgrade. Downgrades are
    always allowed without TOTP. The endpoint computes the gate snapshot
    server-side from shadow_trades + live_trades to avoid trusting a
    client-supplied snapshot.
    """

    new_mode: Literal["manual", "telegram-approve", "fully-auto"]
    totp_code: str | None = None
    reason: str | None = None


class TradingModeChangeOut(BaseModel):
    new_mode: Literal["manual", "telegram-approve", "fully-auto"]
    old_mode: Literal["manual", "telegram-approve", "fully-auto"]
    audit_row_hash: str
    is_upgrade: bool


# --- SP-8 Phase J: kill switch state -------------------------------------

KillSwitchName = Literal[
    "daily_loss",
    "consecutive_losses",
    "network_outage",
    "slippage",
    "liquidation_near",
    "funding_rate_guard",
]


class KillSwitchOut(BaseModel):
    name: KillSwitchName
    enabled: bool
    threshold_value: float | None
    is_tripped: bool
    tripped_at: datetime | None
    tripped_reason: str | None
    default_threshold: float


class KillSwitchListOut(BaseModel):
    switches: list[KillSwitchOut]


class KillSwitchPatchIn(BaseModel):
    """Patch one kill switch. Disabling requires totp_code."""
    enabled: bool | None = None
    threshold_value: float | None = None
    totp_code: str | None = None


class TaxSummaryOut(BaseModel):
    fy_year: str
    total_trades: int
    total_realized_pnl_inr: float
    total_tds_inr: float
    total_fees_inr: float


# --- SP-1 Phase F: ML checkpoint admin schemas (spec §6.4) ----------------


class MlCheckpointOut(BaseModel):
    id: int
    model_name: str
    version: str
    checkpoint_uri: str
    sha256: str
    trained_at: datetime
    train_data_window: str
    eval_results: dict
    is_active: bool
    activated_at: datetime | None = None
    deactivated_at: datetime | None = None
    notes: str | None = None


class MlCheckpointCreateIn(BaseModel):
    model_name: str
    version: str
    checkpoint_uri: str
    sha256: str = Field(min_length=64, max_length=64)
    trained_at: datetime
    train_data_window: str
    eval_results: dict
    notes: str | None = None


class MlCheckpointPatchIn(BaseModel):
    is_active: bool | None = None
    notes: str | None = None


# --- SP-4 Phase D: RL checkpoint admin schemas (spec §6.4) ------------------
# Mirror of Ml*; separate types so the FastAPI router validates the right
# eval_results shape (Sharpe / Sortino / max_dd vs MAE per regime) and so
# OpenAPI docs distinguish the two registries.


class RlCheckpointOut(BaseModel):
    id: int
    model_name: str
    version: str
    checkpoint_uri: str
    sha256: str
    trained_at: datetime
    train_data_window: str
    eval_results: dict
    is_active: bool
    activated_at: datetime | None = None
    deactivated_at: datetime | None = None
    notes: str | None = None


class RlCheckpointCreateIn(BaseModel):
    model_name: str
    version: str
    checkpoint_uri: str
    sha256: str = Field(min_length=64, max_length=64)
    trained_at: datetime
    train_data_window: str
    eval_results: dict
    notes: str | None = None


class RlCheckpointPatchIn(BaseModel):
    is_active: bool | None = None
    notes: str | None = None


# --- SP-2 Phase E: Pattern admin schemas (spec §3.2/§5) -------------------


class PatternEntryOut(BaseModel):
    """One row for the admin list view of all 158 patterns."""

    pattern_id: str
    pattern_type: Literal["candle", "chart"]
    symbol: str = "*"
    timeframe: str = "*"
    enabled: bool = True
    disabled_reason: str | None = None


class PatternToggleIn(BaseModel):
    """Body for /disable and /enable. All fields optional.

    Defaulting ``symbol`` and ``timeframe`` to ``"*"`` matches the global-scope
    convention used by ``pattern_enabled``.
    """

    symbol: str | None = None
    timeframe: str | None = None
    reason: str | None = None


# --- SP-5 Phase F2: Trap admin schemas ------------------------------------


class TrapEntryOut(BaseModel):
    """One row for the admin list view of all 17 trap detectors.

    Mirrors :class:`PatternEntryOut`: per-(trap_id, symbol, timeframe) row
    where the absence of a ``trap_enabled`` row means default-enabled.
    """

    trap_id: str
    severity: Literal["medium", "high", "extreme"]
    side: Literal["long", "short", "both"]
    symbol: str = "*"
    timeframe: str = "*"
    enabled: bool = True
    disabled_reason: str | None = None


class TrapToggleIn(BaseModel):
    """Body for /traps/{id}/disable and /traps/{id}/enable."""

    symbol: str | None = None
    timeframe: str | None = None
    reason: str | None = None


# --- SP-3 Phase F: adapter / universe schemas ---------------------------------


class AdapterHealthOut(BaseModel):
    """Latest health-probe row for a data adapter (returned by admin route)."""

    exchange: str
    checked_at: datetime
    is_healthy: bool
    latency_ms: int | None = None
    error_message: str | None = None
    quota_used_pct: float | None = Field(default=None, ge=0.0, le=1.0)


class UniverseEntryOut(BaseModel):
    """A single ``universe_history`` row exposed by the admin universe route."""

    exchange: str
    symbol: str
    asset_class: Literal["crypto", "stock", "fx", "commodity", "index"]
    listed_at: datetime
    delisted_at: datetime | None
    last_synced_at: datetime

    @property
    def is_active(self) -> bool:
        return self.delisted_at is None


class SyncResultOut(BaseModel):
    """Per-exchange counters returned by the admin sync trigger route."""

    exchange: str
    added: int
    still_active: int
    newly_delisted: int


# --- SP-6 Phase A3: Tab 3 Scanner Radar schemas (spec §3.6) -----------------


class SignalCardScores(BaseModel):
    """Per-card mini score breakdown (rendered as 4 chips on row 5)."""

    smc: int = 0
    wyckoff: int = 0
    microstructure: int = 0
    momentum: int = 0


class SignalCardOut(BaseModel):
    """One row in the bullish or bearish column of Tab 3 Scanner Radar.

    Mirrors MASTER_PLAN §9 line 327 signal-card spec: 5 rows of metadata that
    the frontend renders as star/symbol/sparkline + tag row + pct + conf bar
    + score chips.
    """

    symbol: str
    full_name: str = ""
    is_favorite: bool = False
    points: int = 0
    pct_change: float = 0.0
    direction: Literal["LONG", "SHORT"]
    htf_direction: Literal["LONG", "SHORT", "NEUTRAL"] = "NEUTRAL"
    signal_tier: Literal["NO_SIGNAL", "PAPER", "SMALL", "STANDARD", "A+"]
    hybrid_flag: Literal["LONG", "SHORT", None] = None
    ai_score: int  # may be negative for SHORT cards (rendered as ±N)
    wyckoff_phase: str = "unknown"
    confidence: int = Field(ge=0, le=100)  # already scaled to 0..100
    scores: SignalCardScores
    sparkline: list[float] = Field(default_factory=list, max_length=20)


class ScannerFilterCounts(BaseModel):
    """Counts for each filter pill (rendered in the toolbar)."""

    all: int = 0
    confirmed: int = 0
    probable: int = 0
    weak: int = 0
    diverging: int = 0


class SupervisorProgress(BaseModel):
    done: int = Field(ge=0, default=0)
    total: int = Field(ge=1, default=8)


class ScannerRadarOut(BaseModel):
    """Top-level Tab 3 Scanner Radar payload (spec §3.6)."""

    scanned_at: datetime
    market: Literal["crypto", "stock", "fx", "commodity", "index"]
    timeframe: str
    scanned_count: int
    filter_counts: ScannerFilterCounts
    supervisor_progress: SupervisorProgress = Field(default_factory=SupervisorProgress)
    bullish: list[SignalCardOut] = Field(default_factory=list)
    bearish: list[SignalCardOut] = Field(default_factory=list)


# --- SP-3.5: Intermarket snapshot ----------------------------------------

class IntermarketSnapshotOut(BaseModel):
    """Latest funding/OI snapshot for a symbol + DXY/gold correlations.

    All numeric fields except ``symbol`` and ``captured_at`` are nullable —
    worker may have only seeded a partial snapshot, or yfinance may be
    unavailable for the macro correlations.
    """

    symbol: str
    funding_rate: float | None = None
    mark_price: float | None = None
    open_interest: float | None = None
    open_interest_delta_24h_pct: float | None = None
    dxy_correlation_30d: float | None = Field(default=None, ge=-1.0, le=1.0)
    gold_correlation_30d: float | None = Field(default=None, ge=-1.0, le=1.0)
    captured_at: datetime


# --- SP-PAUSE: master pause/resume -----------------------------------

class SystemPauseRequest(BaseModel):
    """Body for POST /api/v1/admin/system/pause. Reason is required."""
    reason: str = Field(min_length=1, max_length=500)


class SystemPauseStateOut(BaseModel):
    paused: bool
    since: datetime | None = None
    by_email: str | None = None
    reason: str | None = None


class SystemPauseEventOut(BaseModel):
    id: int
    kind: Literal["system_paused", "system_resumed"]
    by_email: str
    at: datetime
    reason: str | None = None


class SystemPauseEventListOut(BaseModel):
    events: list[SystemPauseEventOut]
