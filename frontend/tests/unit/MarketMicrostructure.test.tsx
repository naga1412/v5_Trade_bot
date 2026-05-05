import { render, screen } from "@testing-library/react";
import { MarketMicrostructure } from "@/tabs/Tab1LivePrediction/panels/MarketMicrostructure";
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
  render(<MarketMicrostructure data={null} />);
  expect(screen.getByText("—")).toBeInTheDocument();
});

test("renders dash when L6 missing", () => {
  render(<MarketMicrostructure data={base} />);
  expect(screen.getByText("—")).toBeInTheDocument();
});

test("renders BUY DOM + parsed imbalance for LONG L6", () => {
  const data = {
    ...base,
    layer_scores: {
      "6": { direction: "LONG" as const, strength: 0.6, confidence: 0.7, notes: "imbalance=2.4" },
    },
  };
  render(<MarketMicrostructure data={data} />);
  expect(screen.getByText("BUY DOM")).toBeInTheDocument();
  expect(screen.getByText("2.4")).toBeInTheDocument();
  expect(screen.getByText("0.60")).toBeInTheDocument();
});

test("renders SELL DOM with negative flow when L6 SHORT", () => {
  const data = {
    ...base,
    layer_scores: {
      "6": { direction: "SHORT" as const, strength: 0.5, confidence: 0.7, notes: "" },
    },
  };
  render(<MarketMicrostructure data={data} />);
  expect(screen.getByText("SELL DOM")).toBeInTheDocument();
  expect(screen.getByText("-0.50")).toBeInTheDocument();
});
