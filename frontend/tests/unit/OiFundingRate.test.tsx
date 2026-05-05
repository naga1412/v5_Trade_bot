import { render, screen } from "@testing-library/react";
import { OiFundingRate } from "@/tabs/Tab1LivePrediction/panels/OiFundingRate";

const base = {
  symbol: "BTC/USDT", timeframe: "1h", ts: "2026-05-01T12:00:00Z",
  price: 100,
  final: { score: 0, direction: "NEUTRAL" as const, confidence: 0, contributing_layers: [] },
  layer_scores: {},
  trade_setup: { direction: "NEUTRAL" as const, entry: null, stop_loss: null, take_profit: null, risk_reward: null },
  momentum: { rsi: null, macd_line: null, macd_signal: null, macd_hist: null },
  cold_start: false, inputs_hash: "x",
};

test("renders 'no data' for OI delta and funding", () => {
  render(<OiFundingRate data={base} />);
  // 2 cells should both render the literal "no data" text.
  expect(screen.getAllByText("no data").length).toBe(2);
});

test("renders 'no data' even when data is null (placeholder is data-independent)", () => {
  render(<OiFundingRate data={null} />);
  expect(screen.getAllByText("no data").length).toBe(2);
});

test("renders OI and Funding labels", () => {
  render(<OiFundingRate data={base} />);
  expect(screen.getByText(/OI delta/i)).toBeInTheDocument();
  expect(screen.getByText(/^Funding$/i)).toBeInTheDocument();
});
