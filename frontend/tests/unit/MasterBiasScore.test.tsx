import { render, screen } from "@testing-library/react";
import { MasterBiasScore } from "@/tabs/Tab1LivePrediction/panels/MasterBiasScore";

const baseMock = {
  symbol: "BTC/USDT", timeframe: "1h", ts: "2026-05-01T12:00:00Z",
  price: 100, layer_scores: {},
  trade_setup: { direction: "NEUTRAL" as const, entry: null, stop_loss: null, take_profit: null, risk_reward: null },
  momentum: { rsi: null, macd_line: null, macd_signal: null, macd_hist: null },
  cold_start: false, inputs_hash: "x",
};

test("shows BULL label when score positive", () => {
  render(<MasterBiasScore data={{ ...baseMock,
    final: { score: 0.5, direction: "LONG", confidence: 0.7, contributing_layers: [] } }} />);
  expect(screen.getByText("BULL")).toBeInTheDocument();
});

test("shows BEAR label when score negative", () => {
  render(<MasterBiasScore data={{ ...baseMock,
    final: { score: -0.5, direction: "SHORT", confidence: 0.7, contributing_layers: [] } }} />);
  expect(screen.getByText("BEAR")).toBeInTheDocument();
});

test("shows NEUTRAL within band", () => {
  render(<MasterBiasScore data={{ ...baseMock,
    final: { score: 0.05, direction: "NEUTRAL", confidence: 0.5, contributing_layers: [] } }} />);
  expect(screen.getByText("NEUTRAL")).toBeInTheDocument();
});
