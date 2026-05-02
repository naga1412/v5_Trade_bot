import { render, screen } from "@testing-library/react";
import { TradeSetup } from "@/tabs/Tab1LivePrediction/panels/TradeSetup";

const data = {
  symbol: "BTC/USDT", timeframe: "1h", ts: "2026-05-01T12:00:00Z", price: 100,
  final: { score: 0.5, direction: "LONG" as const, confidence: 0.7, contributing_layers: [1] },
  layer_scores: {},
  trade_setup: { direction: "LONG" as const, entry: 100.0, stop_loss: 95.0, take_profit: 110.0, risk_reward: 2.0 },
  momentum: { rsi: null, macd_line: null, macd_signal: null, macd_hist: null },
  cold_start: false, inputs_hash: "x",
};

test("renders entry/stop/tp/rr", () => {
  render(<TradeSetup data={data} />);
  expect(screen.getByText("100.00")).toBeInTheDocument();
  expect(screen.getByText("95.00")).toBeInTheDocument();
  expect(screen.getByText("110.00")).toBeInTheDocument();
  expect(screen.getByText("2.00")).toBeInTheDocument();
});
