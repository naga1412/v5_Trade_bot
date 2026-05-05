import { render, screen } from "@testing-library/react";
import { LongShortRatio } from "@/tabs/Tab1LivePrediction/panels/LongShortRatio";
import type { LayerScore } from "@/lib/api";

const layer = (direction: LayerScore["direction"]): LayerScore => ({
  direction,
  strength: 0.5,
  confidence: 0.5,
  notes: "",
});

const base = {
  symbol: "BTC/USDT", timeframe: "1h", ts: "2026-05-01T12:00:00Z",
  price: 100,
  final: { score: 0, direction: "NEUTRAL" as const, confidence: 0, contributing_layers: [] },
  layer_scores: {} as Record<string, LayerScore | null>,
  trade_setup: { direction: "NEUTRAL" as const, entry: null, stop_loss: null, take_profit: null, risk_reward: null },
  momentum: { rsi: null, macd_line: null, macd_signal: null, macd_hist: null },
  cold_start: false, inputs_hash: "x",
};

test("renders empty state when data is null", () => {
  render(<LongShortRatio data={null} />);
  expect(screen.getByText("—")).toBeInTheDocument();
});

test("renders 66.7 / 33.3 when 2 LONG + 1 SHORT", () => {
  const data = {
    ...base,
    layer_scores: { "1": layer("LONG"), "3": layer("SHORT"), "4": layer("LONG") },
  };
  render(<LongShortRatio data={data} />);
  expect(screen.getByText("66.7")).toBeInTheDocument();
  expect(screen.getByText("33.3")).toBeInTheDocument();
});

test("falls back to 50/50 when no LONG or SHORT layers", () => {
  const data = {
    ...base,
    layer_scores: { "1": layer("NEUTRAL"), "2": null },
  };
  render(<LongShortRatio data={data} />);
  expect(screen.getAllByText("50.0").length).toBe(2);
});
