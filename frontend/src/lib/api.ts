const BASE = (import.meta.env.VITE_API_URL ?? "/api/v1") as string;

export async function fetchJson<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { credentials: "include" });
  if (!res.ok) throw new Error(`HTTP ${res.status} for ${path}`);
  return (await res.json()) as T;
}

export const api = {
  health: () => fetchJson<{ status: string; version: string }>("/health"),
  predict: (symbolPath: string, tf: string) =>
    fetchJson<LivePrediction>(`/predict/${symbolPath}/${tf}`),
};

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
