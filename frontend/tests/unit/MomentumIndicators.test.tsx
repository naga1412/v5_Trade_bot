import { render, screen } from "@testing-library/react";
import { MomentumIndicators } from "@/tabs/Tab1LivePrediction/panels/MomentumIndicators";

const data = {
  symbol: "BTC/USDT", timeframe: "1h", ts: "2026-05-01T12:00:00Z",
  price: 100,
  final: { score: 0, direction: "NEUTRAL" as const, confidence: 0, contributing_layers: [] },
  layer_scores: {},
  trade_setup: { direction: "NEUTRAL" as const, entry: null, stop_loss: null, take_profit: null, risk_reward: null },
  momentum: { rsi: 58.2, macd_line: 0.7321, macd_signal: 0.5012, macd_hist: 0.231 },
  cold_start: true, inputs_hash: "x",
};

test("renders momentum values", () => {
  render(<MomentumIndicators data={data} />);
  expect(screen.getByText("58.2")).toBeInTheDocument();
  expect(screen.getByText("0.7321")).toBeInTheDocument();
});

test("renders dashes when null momentum", () => {
  render(<MomentumIndicators data={{ ...data, momentum: { rsi: null, macd_line: null, macd_signal: null, macd_hist: null } }} />);
  // Four dashes for four metrics
  expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(4);
});
