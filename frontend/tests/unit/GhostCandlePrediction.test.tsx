import { render, screen } from "@testing-library/react";
import { GhostCandlePrediction } from "@/tabs/Tab1LivePrediction/panels/GhostCandlePrediction";
import type { LayerScore, GhostCandle } from "@/lib/api";

const base = {
  symbol: "BTC/USDT", timeframe: "1h", ts: "2026-05-01T12:00:00Z",
  price: 100,
  final: { score: 0, direction: "NEUTRAL" as const, confidence: 0, contributing_layers: [] },
  layer_scores: {} as Record<string, LayerScore | null>,
  trade_setup: { direction: "NEUTRAL" as const, entry: null, stop_loss: null, take_profit: null, risk_reward: null },
  momentum: { rsi: null, macd_line: null, macd_signal: null, macd_hist: null },
  cold_start: false, inputs_hash: "x",
};

const ghost: GhostCandle = {
  open: 100,
  high: 102,
  low: 99,
  close: 101.5,
  p5_low: 98,
  p95_high: 103,
  uncertainty: 0.42,
};

test("renders 'no model' Panel when data.ghost is null", () => {
  render(<GhostCandlePrediction data={{ ...base, ghost: null }} />);
  expect(screen.getByText(/no model/i)).toBeInTheDocument();
});

test("renders 'no model' when data is null", () => {
  render(<GhostCandlePrediction data={null} />);
  expect(screen.getByText(/no model/i)).toBeInTheDocument();
});

test("renders predicted close, % change vs current price, uncertainty band", () => {
  render(<GhostCandlePrediction data={{ ...base, price: 100, ghost }} />);
  // predicted close
  expect(screen.getByText(/101\.50/)).toBeInTheDocument();
  // % change vs current = (101.5 - 100) / 100 = +1.5%
  expect(screen.getByText(/\+1\.50%/)).toBeInTheDocument();
  // uncertainty value should appear
  expect(screen.getByText(/0\.42/)).toBeInTheDocument();
});

test("renders negative % change with red sign for bearish ghost", () => {
  const bearishGhost: GhostCandle = { ...ghost, close: 98.5 };
  render(<GhostCandlePrediction data={{ ...base, price: 100, ghost: bearishGhost }} />);
  expect(screen.getByText(/-1\.50%/)).toBeInTheDocument();
});
