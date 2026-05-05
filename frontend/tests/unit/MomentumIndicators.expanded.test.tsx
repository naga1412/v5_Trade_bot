import { render, screen } from "@testing-library/react";
import { MomentumIndicators } from "@/tabs/Tab1LivePrediction/panels/MomentumIndicators";

const base = {
  symbol: "BTC/USDT", timeframe: "1h", ts: "x", price: 100,
  final: { score: 0, direction: "NEUTRAL" as const, confidence: 0, contributing_layers: [] },
  layer_scores: {},
  trade_setup: { direction: "NEUTRAL" as const, entry: null, stop_loss: null, take_profit: null, risk_reward: null },
  momentum: { rsi: 58.2, macd_line: 0.7, macd_signal: 0.5, macd_hist: 0.2 },
  cold_start: false, inputs_hash: "x",
};

test("renders Stoch and CCI placeholder rows", () => {
  render(<MomentumIndicators data={base} />);
  expect(screen.getByText(/Stoch/i)).toBeInTheDocument();
  expect(screen.getByText(/CCI/i)).toBeInTheDocument();
});

test("Stoch and CCI cells are em-dashes (placeholder)", () => {
  render(<MomentumIndicators data={base} />);
  // RSI = 58.2, MACD trio render real values; Stoch + CCI should be the
  // only em-dash cells in this fixture.
  const dashes = screen.getAllByText("—");
  expect(dashes.length).toBeGreaterThanOrEqual(2);
});
