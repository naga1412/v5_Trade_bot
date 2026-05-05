import { render, screen } from "@testing-library/react";
import { HtfBiasStructure } from "@/tabs/Tab1LivePrediction/panels/HtfBiasStructure";
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
  render(<HtfBiasStructure data={null} />);
  expect(screen.getByText("—")).toBeInTheDocument();
});

test("renders dash when layer 1 missing", () => {
  render(<HtfBiasStructure data={base} />);
  expect(screen.getByText("—")).toBeInTheDocument();
});

test("renders Wyckoff phase + green LONG when L1 = LONG / Markup", () => {
  const data = {
    ...base,
    layer_scores: {
      "1": { direction: "LONG" as const, strength: 0.6, confidence: 0.7, notes: "Wyckoff phase: Markup" },
    },
  };
  render(<HtfBiasStructure data={data} />);
  expect(screen.getByText("LONG")).toHaveClass("text-green");
  expect(screen.getByText("Markup")).toBeInTheDocument();
  expect(screen.getByText("70%")).toBeInTheDocument();
});

test("renders 'unknown' Wyckoff when notes missing phase keyword", () => {
  const data = {
    ...base,
    layer_scores: {
      "1": { direction: "SHORT" as const, strength: 0.4, confidence: 0.6, notes: "no phase here" },
    },
  };
  render(<HtfBiasStructure data={data} />);
  expect(screen.getByText("unknown")).toBeInTheDocument();
});
