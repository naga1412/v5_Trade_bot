import { render, screen } from "@testing-library/react";
import { NewsMacroImpact } from "@/tabs/Tab1LivePrediction/panels/NewsMacroImpact";
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

test("renders 'no events' when data is null", () => {
  render(<NewsMacroImpact data={null} />);
  expect(screen.getByText(/no events/i)).toBeInTheDocument();
});

test("renders 'no events' as the placeholder body (news adapter not wired)", () => {
  render(<NewsMacroImpact data={base} />);
  expect(screen.getByText(/no events/i)).toBeInTheDocument();
});

test("renders the News & Macro section title", () => {
  const { container } = render(<NewsMacroImpact data={base} />);
  // Panel renders the title in the header h3
  const header = container.querySelector("h3");
  expect(header?.textContent).toMatch(/news/i);
});
