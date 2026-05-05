import { render, screen } from "@testing-library/react";
import { DeepLearningSupervisor } from "@/tabs/Tab1LivePrediction/panels/DeepLearningSupervisor";
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

test("renders nothing when data is null", () => {
  const { container } = render(<DeepLearningSupervisor data={null} />);
  expect(container.firstChild).toBeNull();
});

test("renders nothing when layer 8 is absent", () => {
  const { container } = render(<DeepLearningSupervisor data={base} />);
  expect(container.firstChild).toBeNull();
});

test("renders alert variant + warning text when SHORT + confidence > 0.75", () => {
  const data = {
    ...base,
    layer_scores: {
      "8": { direction: "SHORT" as const, strength: 0.9, confidence: 0.85, notes: "bear engulf" },
    },
  };
  render(<DeepLearningSupervisor data={data} />);
  expect(screen.getByText("SHORT")).toHaveClass("text-red");
  expect(screen.getByText(/SHORT alert/i)).toBeInTheDocument();
});

test("renders normal variant for LONG without alert text", () => {
  const data = {
    ...base,
    layer_scores: {
      "8": { direction: "LONG" as const, strength: 0.7, confidence: 0.8, notes: "" },
    },
  };
  render(<DeepLearningSupervisor data={data} />);
  expect(screen.getByText("LONG")).toHaveClass("text-green");
  expect(screen.queryByText(/SHORT alert/i)).not.toBeInTheDocument();
});
