import { render, screen } from "@testing-library/react";
import { SentimentFearGreed } from "@/tabs/Tab1LivePrediction/panels/SentimentFearGreed";
import type { LayerScore, LivePrediction } from "@/lib/api";

const baseNoSent: LivePrediction = {
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

test("renders 'no data' when data is null", () => {
  render(<SentimentFearGreed data={null} />);
  expect(screen.getAllByText("no data").length).toBeGreaterThan(0);
});

test("renders 'no data' as the placeholder body when sentiment is missing", () => {
  render(<SentimentFearGreed data={baseNoSent} />);
  expect(screen.getAllByText("no data").length).toBe(2);
});

test("renders Fear/Greed label and Sentiment label", () => {
  render(<SentimentFearGreed data={baseNoSent} />);
  expect(screen.getByText(/Fear & Greed/i)).toBeInTheDocument();
  expect(screen.getByText(/^Sentiment$/i)).toBeInTheDocument();
});

test("renders F&G value + label + news bias when sentiment present", () => {
  const data: LivePrediction = {
    ...baseNoSent,
    sentiment: {
      fng_value: 70, fng_label: "Greed", news_bias: "Bullish",
    },
  };
  render(<SentimentFearGreed data={data} />);
  expect(screen.getByText(/70 · Greed/)).toBeInTheDocument();
  expect(screen.getByText(/Bullish/)).toBeInTheDocument();
});

test("F&G uses red color tier for value <=25", () => {
  const data: LivePrediction = {
    ...baseNoSent,
    sentiment: {
      fng_value: 15, fng_label: "Extreme Fear", news_bias: "Bearish",
    },
  };
  const { container } = render(<SentimentFearGreed data={data} />);
  expect(container.querySelector(".text-red-400")).not.toBeNull();
});

test("F&G uses green color tier for value >=56", () => {
  const data: LivePrediction = {
    ...baseNoSent,
    sentiment: {
      fng_value: 80, fng_label: "Extreme Greed", news_bias: "Bullish",
    },
  };
  const { container } = render(<SentimentFearGreed data={data} />);
  expect(container.querySelector(".text-green-400")).not.toBeNull();
});

test("News bias 'Bearish' uses red color tier", () => {
  const data: LivePrediction = {
    ...baseNoSent,
    sentiment: {
      fng_value: 50, fng_label: "Neutral", news_bias: "Bearish",
    },
  };
  const { container } = render(<SentimentFearGreed data={data} />);
  // Bearish bias label uses text-red-400.
  const bearish = screen.getByText(/Bearish/);
  expect(bearish.className).toMatch(/text-red-400/);
  // And the F&G value sits in the gray (text-text-secondary) tier.
  expect(container.textContent).toMatch(/50/);
});
