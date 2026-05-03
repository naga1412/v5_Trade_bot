const BASE = (import.meta.env.VITE_API_URL ?? "/api/v1") as string;

export async function fetchJson<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { credentials: "include" });
  if (!res.ok) throw new Error(`HTTP ${res.status} for ${path}`);
  return (await res.json()) as T;
}

export interface LayerScore {
  direction: "LONG" | "SHORT" | "NEUTRAL";
  strength: number;
  confidence: number;
  notes: string;
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

export const api = {
  health: () => fetchJson<{ status: string; version: string }>("/health"),
  predict: (symbolPath: string, tf: string) =>
    fetchJson<LivePrediction>(`/predict/${symbolPath}/${tf}`),
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
};
