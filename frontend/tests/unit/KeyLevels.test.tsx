import { render, screen } from "@testing-library/react";
import { KeyLevels } from "@/tabs/Tab1LivePrediction/panels/KeyLevels";
import type { LayerScore } from "@/lib/api";

const base = {
  symbol: "BTC/USDT", timeframe: "1h", ts: "2026-05-01T12:00:00Z",
  price: 100,
  final: { score: 0, direction: "NEUTRAL" as const, confidence: 0, contributing_layers: [] },
  layer_scores: {} as Record<string, LayerScore | null>,
  trade_setup: { direction: "NEUTRAL" as const, entry: null, stop_loss: null, take_profit: null, risk_reward: null },
  momentum: { rsi: null, macd_line: null, macd_signal: null, macd_hist: null },
  cold_start: false, inputs_hash: "x",
};

test("renders dash when data is null", () => {
  render(<KeyLevels data={null} />);
  expect(screen.getByText("—")).toBeInTheDocument();
});

test("renders EMA20/50/200 labels with em-dash when backend doesn't expose values", () => {
  render(<KeyLevels data={base} />);
  expect(screen.getByText(/^EMA20$/)).toBeInTheDocument();
  expect(screen.getByText(/^EMA50$/)).toBeInTheDocument();
  expect(screen.getByText(/^EMA200$/)).toBeInTheDocument();
  // 3 em-dash placeholders inside the panel body for the values
  expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(3);
});

test("renders 'EMAs aligned ascending' note from L1 layer when present", () => {
  const data = {
    ...base,
    layer_scores: {
      "1": {
        direction: "LONG" as const,
        strength: 0.9,
        confidence: 0.85,
        notes: "EMAs aligned ascending",
      },
    },
  };
  render(<KeyLevels data={data} />);
  expect(screen.getByText(/EMAs aligned ascending/i)).toBeInTheDocument();
});
