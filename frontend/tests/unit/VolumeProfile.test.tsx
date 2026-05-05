import { render, screen } from "@testing-library/react";
import { VolumeProfile } from "@/tabs/Tab1LivePrediction/panels/VolumeProfile";
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

test("renders empty state when data is null", () => {
  render(<VolumeProfile data={null} />);
  expect(screen.getByText("—")).toBeInTheDocument();
});

test("renders three placeholders when L5 notes lack POC pattern", () => {
  render(<VolumeProfile data={base} />);
  // 3 dashes for POC/VAH/VAL.
  expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(3);
});

test("parses POC / VAH / VAL when notes match", () => {
  const data = {
    ...base,
    layer_scores: {
      "5": {
        direction: "NEUTRAL" as const,
        strength: 0.5,
        confidence: 0.6,
        notes: "POC=68500 VAH=69000 VAL=68000",
      },
    },
  };
  render(<VolumeProfile data={data} />);
  expect(screen.getByText("68500")).toBeInTheDocument();
  expect(screen.getByText("69000")).toBeInTheDocument();
  expect(screen.getByText("68000")).toBeInTheDocument();
});
