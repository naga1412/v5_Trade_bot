import { render, screen } from "@testing-library/react";
import { FinalValue } from "@/tabs/Tab1LivePrediction/panels/FinalValue";

const base = {
  symbol: "BTC/USDT", timeframe: "1h", ts: "2026-05-01T12:00:00Z",
  price: 100,
  final: { score: 0.6, direction: "LONG" as const, confidence: 0.72, contributing_layers: [] },
  layer_scores: {},
  trade_setup: {
    direction: "LONG" as const,
    entry: 100, stop_loss: 95, take_profit: 110, risk_reward: 2.6,
  },
  momentum: { rsi: null, macd_line: null, macd_signal: null, macd_hist: null },
  cold_start: false, inputs_hash: "x",
};

test("renders R:R and match-strict % from populated data", () => {
  render(<FinalValue data={base} />);
  expect(screen.getByText("2.6")).toBeInTheDocument();
  expect(screen.getByText("72%")).toBeInTheDocument();
});

test("renders empty state when data is null", () => {
  render(<FinalValue data={null} />);
  expect(screen.getByText("—")).toBeInTheDocument();
});

test("renders dash when risk_reward is null", () => {
  const data = { ...base, trade_setup: { ...base.trade_setup, risk_reward: null } };
  render(<FinalValue data={data} />);
  // Two dashes: risk_reward + max-dd placeholder.
  expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(2);
});
