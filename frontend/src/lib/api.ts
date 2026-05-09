const BASE = (import.meta.env.VITE_API_URL ?? "/api/v1") as string;

export interface FetchOptions {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  body?: unknown;
}

export async function fetchJson<T>(
  path: string,
  options?: FetchOptions,
): Promise<T> {
  const init: RequestInit = {
    credentials: "include",
    method: options?.method ?? "GET",
  };
  if (options?.body !== undefined) {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(options.body);
  }
  const res = await fetch(`${BASE}${path}`, init);
  if (!res.ok) throw new Error(`HTTP ${res.status} for ${path}`);
  // 204 No Content → no body to parse; return undefined as T (callers should
  // declare the response type as `void` for those endpoints).
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export interface LayerScore {
  direction: "LONG" | "SHORT" | "NEUTRAL";
  strength: number;
  confidence: number;
  notes: string;
}

export interface SignalMarkers {
  signal_id: string;
  direction: "LONG" | "SHORT";
  entry_price: number;
  stop_loss: number;
  take_profit: number;
  opened_at: string;          // ISO datetime
  closed_at: string | null;
  exit_price: number | null;
  exit_reason: "TAKE_PROFIT" | "STOP_LOSS" | "TIMEOUT" | null;
}

// SP-1: predicted next-bar ghost candle + uncertainty band.
export interface GhostCandle {
  open: number;
  high: number;
  low: number;
  close: number;
  p5_low: number;
  p95_high: number;
  uncertainty: number; // [0, ∞), lower = more confident
}

// SP-9 Phase F2: F&G index + L9 news bias summary surfaced on Tab 1.
export interface SentimentSummary {
  fng_value: number;
  fng_label:
    | "Extreme Fear"
    | "Fear"
    | "Neutral"
    | "Greed"
    | "Extreme Greed";
  news_bias: "Bullish" | "Bearish" | "Neutral";
}

// SP-9 Phase F2: top recent-headline + impact aggregation surfaced on Tab 1.
export interface NewsSummary {
  recent_count: number;
  top_headline: string | null;
  impact: "LOW" | "MEDIUM" | "HIGH";
}

export interface LivePrediction {
  symbol: string;
  timeframe: string;
  ts: string;
  price: number;
  final: {
    score: number;
    direction: "LONG" | "SHORT" | "NEUTRAL";
    confidence: number;
    contributing_layers: number[];
  };
  layer_scores: Record<string, LayerScore | null>;
  trade_setup: {
    direction: "LONG" | "SHORT" | "NEUTRAL";
    entry: number | null;
    stop_loss: number | null;
    take_profit: number | null;
    risk_reward: number | null;
  };
  momentum: {
    rsi: number | null;
    macd_line: number | null;
    macd_signal: number | null;
    macd_hist: number | null;
  };
  cold_start: boolean;
  inputs_hash: string;
  signal_markers?: SignalMarkers | null;
  ghost?: GhostCandle | null; // SP-1: null when no active ML checkpoint loaded.
  sentiment?: SentimentSummary | null; // SP-9
  news?: NewsSummary | null;            // SP-9
}

// --- Bot Status types ---

export interface WindowStats {
  window: "24h" | "7d" | "30d" | "lifetime";
  trades: number;
  pnl_usdt: number;
  pnl_pct: number | null;
  win_rate: number;
  sharpe_annualized: number | null;
  max_drawdown: number;
  profit_factor: number;
}

export interface BotOverview {
  last_24h: WindowStats;
  last_7d: WindowStats;
  last_30d: WindowStats;
  long_only_30d: WindowStats;
  short_only_30d: WindowStats;
}

export interface GateMetric {
  name: string;
  current: number | null;
  threshold: number;
  operator: ">=" | "<=";
  passing: boolean;
}

export interface PromotionGate {
  target_mode: "telegram-approve" | "fully-auto";
  metrics: GateMetric[];
  all_passing: boolean;
  distance_summary: string;
}

export interface OpenPosition {
  symbol: string;
  direction: "LONG" | "SHORT";
  entry_price: number;
  stop_loss: number;
  take_profit: number;
  position_size_usdt: number;
  bars_held: number;
  opened_at: string;            // ISO datetime
  signal_id: string;
  current_price: number | null;
  unrealized_pnl_pct: number | null;
  unrealized_pnl_usdt: number | null;
}

export interface PerAssetStat {
  symbol: string;
  trades: number;
  win_rate: number;
  avg_rr: number;
  pnl_usdt: number;
  sharpe_annualized: number | null;
}

export interface RecentTrade {
  closed_at: string;            // ISO datetime
  symbol: string;
  direction: "LONG" | "SHORT";
  entry_price: number;
  exit_price: number;
  pnl_pct: number;
  pnl_usdt: number;
  exit_reason: "TAKE_PROFIT" | "STOP_LOSS" | "TIMEOUT";
  bars_held: number;
  signal_id: string;
}

export interface LongShortBreakdown {
  long: WindowStats;
  short: WindowStats;
}

export interface EquityCurvePoint {
  date: string;                 // ISO datetime
  cumulative_pnl_usdt: number;
}

export interface EquityCurve {
  days: number;
  points: EquityCurvePoint[];
}

export interface AssetUniverseEntry {
  symbol: string;
  rank: number;
  quote_volume_24h_usdt: number;
}

export interface AssetUniverse {
  snapshot_at: string;          // ISO datetime
  entries: AssetUniverseEntry[];
}

export interface RecentTradesFilters {
  limit?: number;
  symbol?: string;              // accepts BTC/USDT (caller-side encoding handled here)
  direction?: "LONG" | "SHORT";
  result?: "win" | "loss";
}

// --- SP-0.7 Phase H: /me types (mirrors backend MeOut Pydantic schema) ---

export type TradingMode = "manual" | "telegram-approve" | "fully-auto";
export type PositionSizingMode = "fixed" | "percentage";

export interface MeOut {
  id: number;
  email: string;
  display_name: string;
  is_admin: boolean;
  is_impersonating: boolean;
  trading_mode: TradingMode;
  position_sizing_mode: PositionSizingMode;
  fixed_size_min_usdt: number | null;
  fixed_size_max_usdt: number | null;
  max_concurrent_positions: number | null;
  max_leverage_cap: number | null;
  quiet_hours_enabled: boolean;
  quiet_hours_start: string | null;
  quiet_hours_end: string | null;
  binance_keys_configured: boolean;
  telegram_configured: boolean;
  totp_configured: boolean;
}

export interface MePatchIn {
  display_name?: string;
  quiet_hours_enabled?: boolean;
  quiet_hours_start?: string | null;
  quiet_hours_end?: string | null;
  fixed_size_min_usdt?: number | null;
  fixed_size_max_usdt?: number | null;
  max_concurrent_positions?: number | null;
  max_leverage_cap?: number | null;
}

export interface TotpSetupOut {
  provisioning_uri: string;
  secret_for_display: string;
  backup_codes: string[];
}

export interface TotpVerifyOut {
  ok: boolean;
}

// --- SP-8 Phase J: trading-mode change ---

export interface TradingModeChangeIn {
  new_mode: TradingMode;
  totp_code?: string;
  reason?: string;
}

export interface TradingModeChangeOut {
  new_mode: TradingMode;
  old_mode: TradingMode;
  audit_row_hash: string;
  is_upgrade: boolean;
}

export interface TradingModeChangeError {
  error: "gate_failed";
  current_mode: TradingMode;
  requested_mode: TradingMode;
  failures: string[];
}

// --- SP-0.7 Phase G: Admin types (mirrors backend Pydantic schemas) ---

export interface AdminUser {
  id: number;
  email: string;
  display_name: string;
  is_admin: boolean;
  is_active: boolean;
  trading_mode: string;
  last_login: string | null;
  created_at: string;
  invited_by: number | null;
}

export interface InvitationCreateIn {
  email: string;
  display_name?: string | null;
  is_admin?: boolean;
  notes?: string | null;
}

export interface Invitation {
  id: number;
  email: string;
  display_name: string | null;
  invited_by: number;
  invited_at: string;
  accepted_at: string | null;
  cf_access_added: boolean;
}

export interface UserPatchIn {
  is_active?: boolean;
  is_admin?: boolean;
  notes?: string;
}

export interface ImpersonationStartOut {
  admin_user_id: number;
  target_user_id: number;
  started_at: string;
}

export interface AuditTrailEntry {
  table_name: string;
  row_id: number;
  user_id: number | null;
  ts: string;
  summary: string;
}

export interface AuditTrailFilters {
  user_id?: number;
  table?: string;
  since?: string;
  limit?: number;
  offset?: number;
}

// --- SP-1 Phase F: ML checkpoint admin types (mirrors backend §6.4) ---

export interface MlCheckpoint {
  id: number;
  model_name: string;
  version: string;
  checkpoint_uri: string;
  sha256: string;
  trained_at: string;
  train_data_window: string;
  eval_results: Record<string, unknown>;
  is_active: boolean;
  activated_at: string | null;
  deactivated_at: string | null;
  notes: string | null;
}

export interface MlCheckpointPatchIn {
  is_active?: boolean;
  notes?: string;
}

// --- SP-6 Phase A5: Scanner + admin sub-page types ---

export interface SignalCardScores {
  smc: number;
  wyckoff: number;
  microstructure: number;
  momentum: number;
}

export interface SignalCard {
  symbol: string;
  full_name: string;
  is_favorite: boolean;
  points: number;
  pct_change: number;
  direction: "LONG" | "SHORT";
  htf_direction: "LONG" | "SHORT" | "NEUTRAL";
  signal_tier: "NO_SIGNAL" | "PAPER" | "SMALL" | "STANDARD" | "A+";
  hybrid_flag: "LONG" | "SHORT" | null;
  ai_score: number;
  wyckoff_phase: string;
  confidence: number;
  scores: SignalCardScores;
  sparkline: number[];
}

export interface ScannerFilterCounts {
  all: number;
  confirmed: number;
  probable: number;
  weak: number;
  diverging: number;
}

export interface SupervisorProgress {
  done: number;
  total: number;
}

export interface ScannerRadar {
  scanned_at: string;
  market: "crypto" | "stock" | "fx" | "commodity" | "index";
  timeframe: string;
  scanned_count: number;
  filter_counts: ScannerFilterCounts;
  supervisor_progress: SupervisorProgress;
  bullish: SignalCard[];
  bearish: SignalCard[];
}

export interface ScannerRadarOptions {
  market?: ScannerRadar["market"];
  tf?: string;
  limit?: number;
}

export interface PatternEntry {
  pattern_id: string;
  pattern_type: "candle" | "chart";
  symbol: string;
  timeframe: string;
  enabled: boolean;
  disabled_reason: string | null;
}

export interface TrapEntry {
  trap_id: string;
  severity: "medium" | "high" | "extreme";
  side: "long" | "short" | "both";
  symbol: string;
  timeframe: string;
  enabled: boolean;
  disabled_reason: string | null;
}

export interface AdapterHealth {
  exchange: string;
  checked_at: string;
  is_healthy: boolean;
  latency_ms: number | null;
  error_message: string | null;
  quota_used_pct: number | null;
}

export interface SyncResult {
  exchange: string;
  added: number;
  still_active: number;
  newly_delisted: number;
}

export const api = {
  health: () => fetchJson<{ status: string; version: string }>("/health"),
  predict: (symbolPath: string, tf: string, signal?: string) => {
    const qs = signal ? `?signal=${encodeURIComponent(signal)}` : "";
    return fetchJson<LivePrediction>(`/predict/${symbolPath}/${tf}${qs}`);
  },
  botOverview: () => fetchJson<BotOverview>("/bot-status/overview"),
  promotionGate: () => fetchJson<PromotionGate>("/bot-status/promotion-gate"),
  openPositions: () => fetchJson<OpenPosition[]>("/bot-status/open-positions"),
  perAssetStats: (days = 30) =>
    fetchJson<PerAssetStat[]>(`/bot-status/per-asset?days=${days}`),
  recentTrades: (f: RecentTradesFilters = {}) => {
    const qs = new URLSearchParams();
    if (f.limit != null) qs.set("limit", String(f.limit));
    if (f.symbol) qs.set("symbol", f.symbol);
    if (f.direction) qs.set("direction", f.direction);
    if (f.result) qs.set("result", f.result);
    const tail = qs.toString();
    return fetchJson<RecentTrade[]>(
      `/bot-status/recent-trades${tail ? "?" + tail : ""}`,
    );
  },
  longShort: (days = 30) =>
    fetchJson<LongShortBreakdown>(`/bot-status/long-vs-short?days=${days}`),
  equityCurve: (days = 30) =>
    fetchJson<EquityCurve>(`/bot-status/equity-curve?days=${days}`),
  assetUniverse: () => fetchJson<AssetUniverse>("/bot-status/asset-universe"),

  // --- SP-0.7 Phase H: self-service /me endpoints ---
  me: () => fetchJson<MeOut>("/me"),
  mePatch: (body: MePatchIn) =>
    fetchJson<MeOut>("/me", { method: "PATCH", body }),
  meBinanceKeys: (api_key: string, api_secret: string) =>
    fetchJson<undefined>("/me/binance-keys", {
      method: "POST",
      body: { api_key, api_secret },
    }),
  meTelegram: (bot_token: string, chat_id: string) =>
    fetchJson<undefined>("/me/telegram", {
      method: "POST",
      body: { bot_token, chat_id },
    }),
  meTotpSetup: () =>
    fetchJson<TotpSetupOut>("/me/totp/setup", { method: "POST" }),
  meTotpVerify: (code: string) =>
    fetchJson<TotpVerifyOut>("/me/totp/verify", {
      method: "POST",
      body: { code },
    }),
  meChangeTradingMode: (body: TradingModeChangeIn) =>
    fetchJson<TradingModeChangeOut>("/me/trading-mode", {
      method: "PATCH",
      body,
    }),

  // --- SP-0.7 Phase G: admin endpoints ---
  adminListUsers: () => fetchJson<AdminUser[]>("/admin/users"),
  adminCreateInvitation: (body: InvitationCreateIn) =>
    fetchJson<Invitation>("/admin/invitations", { method: "POST", body }),
  adminPatchUser: (id: number, body: UserPatchIn) =>
    fetchJson<AdminUser>(`/admin/users/${id}`, { method: "PATCH", body }),
  adminDeleteUser: (id: number) =>
    fetchJson<undefined>(`/admin/users/${id}`, { method: "DELETE" }),
  adminImpersonate: (id: number) =>
    fetchJson<ImpersonationStartOut>(`/admin/impersonate/${id}`, {
      method: "POST",
    }),
  adminImpersonateClear: () =>
    fetchJson<undefined>("/admin/impersonate", { method: "DELETE" }),
  // --- SP-1 Phase F: admin ML checkpoint endpoints ---
  adminListMlCheckpoints: () =>
    fetchJson<MlCheckpoint[]>("/admin/ml-checkpoints"),
  adminPatchMlCheckpoint: (id: number, body: MlCheckpointPatchIn) =>
    fetchJson<MlCheckpoint>(`/admin/ml-checkpoints/${id}`, {
      method: "PATCH",
      body,
    }),
  adminDeleteMlCheckpoint: (id: number) =>
    fetchJson<undefined>(`/admin/ml-checkpoints/${id}`, { method: "DELETE" }),

  adminAuditTrail: (filters: AuditTrailFilters = {}) => {
    const qs = new URLSearchParams();
    if (filters.user_id != null) qs.set("user_id", String(filters.user_id));
    if (filters.table) qs.set("table", filters.table);
    if (filters.since) qs.set("since", filters.since);
    if (filters.limit != null) qs.set("limit", String(filters.limit));
    if (filters.offset != null) qs.set("offset", String(filters.offset));
    const tail = qs.toString();
    return fetchJson<AuditTrailEntry[]>(
      `/admin/audit-trail${tail ? "?" + tail : ""}`,
    );
  },

  // --- SP-6 Phase A5: scanner ---
  scannerRadar: (opts: ScannerRadarOptions = {}) => {
    const market = opts.market ?? "crypto";
    const tf = opts.tf ?? "1h";
    const limit = opts.limit ?? 200;
    return fetchJson<ScannerRadar>(
      `/scanner/radar?market=${market}&tf=${tf}&limit=${limit}`,
    );
  },

  // --- SP-6 Phase A5: admin sub-pages ---
  adminListPatterns: () => fetchJson<PatternEntry[]>("/admin/patterns"),
  adminTogglePattern: (id: string, enable: boolean, reason?: string) =>
    fetchJson<PatternEntry>(
      `/admin/patterns/${encodeURIComponent(id)}/${enable ? "enable" : "disable"}`,
      { method: "POST", body: enable ? {} : { reason: reason ?? "" } },
    ),
  adminListAdapters: () => fetchJson<AdapterHealth[]>("/admin/adapters/health"),
  adminSyncAdapter: (exchange: string) =>
    fetchJson<SyncResult>(
      `/admin/adapters/${encodeURIComponent(exchange)}/sync`,
      { method: "POST" },
    ),
  adminListTraps: () => fetchJson<TrapEntry[]>("/admin/traps"),
  adminToggleTrap: (id: string, enable: boolean, reason?: string) =>
    fetchJson<TrapEntry>(
      `/admin/traps/${encodeURIComponent(id)}/${enable ? "enable" : "disable"}`,
      { method: "POST", body: enable ? {} : { reason: reason ?? "" } },
    ),
};

// SP-3.5 Phase E3: intermarket snapshot
export interface IntermarketSnapshot {
  symbol: string;
  funding_rate: number | null;
  mark_price: number | null;
  open_interest: number | null;
  open_interest_delta_24h_pct: number | null;
  dxy_correlation_30d: number | null;
  gold_correlation_30d: number | null;
  captured_at: string; // ISO 8601 UTC
}

export async function getIntermarket(symbol: string): Promise<IntermarketSnapshot> {
  const url = `/api/v1/intermarket/${encodeURIComponent(symbol)}`;
  const r = await fetch(url, { credentials: "include" });
  if (!r.ok) {
    throw new Error(`getIntermarket(${symbol}) failed: ${r.status}`);
  }
  return r.json() as Promise<IntermarketSnapshot>;
}

// SP-PAUSE: master pause/resume
export interface SystemPauseState {
  paused: boolean;
  since: string | null;       // ISO 8601 UTC
  by_email: string | null;
  reason: string | null;
}

export interface SystemPauseEvent {
  id: number;
  kind: "system_paused" | "system_resumed";
  by_email: string;
  at: string;                  // ISO 8601 UTC
  reason: string | null;
}

export interface SystemPauseEventList {
  events: SystemPauseEvent[];
}

export async function getSystemState(): Promise<SystemPauseState> {
  return fetchJson<SystemPauseState>("/admin/system/state");
}

export async function getSystemLog(
  limit: number = 50,
): Promise<SystemPauseEventList> {
  return fetchJson<SystemPauseEventList>(
    `/admin/system/log?limit=${encodeURIComponent(String(limit))}`,
  );
}

export async function pauseSystem(reason: string): Promise<SystemPauseState> {
  return fetchJson<SystemPauseState>("/admin/system/pause", {
    method: "POST",
    body: { reason },
  });
}

export async function resumeSystem(): Promise<SystemPauseState> {
  return fetchJson<SystemPauseState>("/admin/system/resume", {
    method: "POST",
    body: {},
  });
}
