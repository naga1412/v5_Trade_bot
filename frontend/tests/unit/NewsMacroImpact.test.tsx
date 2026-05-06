import { render, screen } from "@testing-library/react";
import { NewsMacroImpact } from "@/tabs/Tab1LivePrediction/panels/NewsMacroImpact";
import type { LayerScore, LivePrediction } from "@/lib/api";

const base: LivePrediction = {
  symbol: "BTC/USDT", timeframe: "1h", ts: "2026-05-01T12:00:00Z",
  price: 100,
  final: { score: 0, direction: "NEUTRAL", confidence: 0, contributing_layers: [] },
  layer_scores: {} as Record<string, LayerScore | null>,
  trade_setup: {
    direction: "NEUTRAL", entry: null, stop_loss: null,
    take_profit: null, risk_reward: null,
  },
  momentum: { rsi: null, macd_line: null, macd_signal: null, macd_hist: null },
  cold_start: false, inputs_hash: "x",
};

test("renders 'no events' when data is null", () => {
  render(<NewsMacroImpact data={null} />);
  expect(screen.getByText(/no events/i)).toBeInTheDocument();
});

test("renders 'no events' as the placeholder when news is missing", () => {
  render(<NewsMacroImpact data={base} />);
  expect(screen.getByText(/no events/i)).toBeInTheDocument();
});

test("renders the News & Macro section title", () => {
  const { container } = render(<NewsMacroImpact data={base} />);
  // Panel renders the title in the header h3
  const header = container.querySelector("h3");
  expect(header?.textContent).toMatch(/news/i);
});

test("renders top headline + impact badge + recent count", () => {
  const data: LivePrediction = {
    ...base,
    news: {
      recent_count: 4,
      top_headline: "SEC files lawsuit against major exchange",
      impact: "MEDIUM",
    },
  };
  render(<NewsMacroImpact data={data} />);
  expect(screen.getByText(/SEC files lawsuit/)).toBeInTheDocument();
  expect(screen.getByText(/MEDIUM/)).toBeInTheDocument();
  expect(screen.getByText(/4/)).toBeInTheDocument();
});

test("HIGH impact triggers red border on the Panel", () => {
  const data: LivePrediction = {
    ...base,
    news: {
      recent_count: 12, top_headline: "MAJOR CRASH", impact: "HIGH",
    },
  };
  const { container } = render(<NewsMacroImpact data={data} />);
  expect(container.querySelector(".border-red-500")).not.toBeNull();
});

test("truncates long headline to 60 chars + ellipsis", () => {
  const longHeadline = "A".repeat(120);
  const data: LivePrediction = {
    ...base,
    news: {
      recent_count: 1, top_headline: longHeadline, impact: "LOW",
    },
  };
  render(<NewsMacroImpact data={data} />);
  // 60 As + ellipsis = length 61. Use the regex to match the produced node.
  const node = screen.getByText(/^A+…$/);
  expect((node.textContent ?? "").length).toBeLessThanOrEqual(61);
});

test("LOW impact uses gray badge styling and no red border", () => {
  const data: LivePrediction = {
    ...base,
    news: {
      recent_count: 2, top_headline: "Quiet day", impact: "LOW",
    },
  };
  const { container } = render(<NewsMacroImpact data={data} />);
  expect(container.querySelector(".border-red-500")).toBeNull();
  // The impact badge is rendered as a span containing the impact label.
  const badge = screen.getByText(/^LOW$/);
  expect(badge.className).toMatch(/text-text-secondary/);
});
