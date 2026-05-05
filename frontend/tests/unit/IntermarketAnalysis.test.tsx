import { render, screen } from "@testing-library/react";
import { IntermarketAnalysis } from "@/tabs/Tab1LivePrediction/panels/IntermarketAnalysis";
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

test("renders 'no data' when data is null", () => {
  render(<IntermarketAnalysis data={null} />);
  expect(screen.getAllByText("no data").length).toBeGreaterThan(0);
});

test("renders 'no data' for DXY/Gold correlation cells (placeholder until backend wires)", () => {
  render(<IntermarketAnalysis data={base} />);
  // both DXY corr and Gold corr should show "no data"
  expect(screen.getAllByText("no data").length).toBe(2);
});

test("renders DXY and Gold labels", () => {
  render(<IntermarketAnalysis data={base} />);
  expect(screen.getByText(/DXY/i)).toBeInTheDocument();
  expect(screen.getByText(/Gold/i)).toBeInTheDocument();
});
