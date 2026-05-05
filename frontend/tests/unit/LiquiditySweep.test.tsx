import { render, screen } from "@testing-library/react";
import { LiquiditySweep } from "@/tabs/Tab1LivePrediction/panels/LiquiditySweep";
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
  render(<LiquiditySweep data={null} />);
  expect(screen.getByText("—")).toBeInTheDocument();
});

test("renders 'no sweep' when L4 missing", () => {
  render(<LiquiditySweep data={base} />);
  expect(screen.getByText("no sweep")).toBeInTheDocument();
});

test("renders 'Above PDH' green when notes mention sweep above PDH", () => {
  const data = {
    ...base,
    layer_scores: {
      "4": { direction: "LONG" as const, strength: 0.6, confidence: 0.7, notes: "OB sweep above PDH" },
    },
  };
  render(<LiquiditySweep data={data} />);
  expect(screen.getByText("Above PDH")).toHaveClass("text-green");
});

test("renders 'Below PDL' red when notes mention sweep below PDL", () => {
  const data = {
    ...base,
    layer_scores: {
      "4": { direction: "SHORT" as const, strength: 0.5, confidence: 0.7, notes: "sweep below PDL" },
    },
  };
  render(<LiquiditySweep data={data} />);
  expect(screen.getByText("Below PDL")).toHaveClass("text-red");
});

test("renders trap-fired alert text when prediction_extras.traps_fired contains liquidity_sweep", () => {
  const data = {
    ...base,
    layer_scores: {
      "4": { direction: "SHORT" as const, strength: 0.5, confidence: 0.7, notes: "sweep below PDL" },
    },
    prediction_extras: { traps_fired: ["liquidity_sweep"] },
  };
  render(<LiquiditySweep data={data} />);
  expect(screen.getByText(/trap fired/i)).toBeInTheDocument();
});
