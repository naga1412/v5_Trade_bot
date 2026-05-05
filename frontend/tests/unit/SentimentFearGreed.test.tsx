import { render, screen } from "@testing-library/react";
import { SentimentFearGreed } from "@/tabs/Tab1LivePrediction/panels/SentimentFearGreed";
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

test("renders 'no data' when data is null", () => {
  render(<SentimentFearGreed data={null} />);
  expect(screen.getAllByText("no data").length).toBeGreaterThan(0);
});

test("renders 'no data' as the placeholder body (F&G index not wired)", () => {
  render(<SentimentFearGreed data={base} />);
  expect(screen.getAllByText("no data").length).toBe(2);
});

test("renders Fear/Greed label and Sentiment label", () => {
  render(<SentimentFearGreed data={base} />);
  expect(screen.getByText(/Fear & Greed/i)).toBeInTheDocument();
  expect(screen.getByText(/^Sentiment$/i)).toBeInTheDocument();
});
