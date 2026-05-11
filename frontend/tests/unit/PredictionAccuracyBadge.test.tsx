import { render, screen, waitFor } from "@testing-library/react";
import { vi, afterEach, test, expect } from "vitest";
import { PredictionAccuracyBadge } from "@/components/PredictionAccuracyBadge";
import * as apiModule from "@/lib/api";

const baseAccuracy = {
  symbol: "BTC/USDT",
  timeframe: "1h",
  validated_count: 0,
  correct_count: 0,
  accuracy_pct: 0,
  pending_count: 0,
  by_direction: {
    LONG: { n: 0, correct: 0, accuracy_pct: 0 },
    SHORT: { n: 0, correct: 0, accuracy_pct: 0 },
    NEUTRAL: { n: 0, correct: 0, accuracy_pct: 0 },
  },
  avg_pnl_pct: null,
  last_validated_at: null,
};

afterEach(() => {
  vi.restoreAllMocks();
});

test("renders 'warming up' when no validated rows yet", async () => {
  vi.spyOn(apiModule.api, "predictionAccuracy").mockResolvedValue({
    ...baseAccuracy,
    validated_count: 0,
    pending_count: 3,
  });
  render(<PredictionAccuracyBadge symbolPath="BTC-USDT" timeframe="1h" />);
  await waitFor(() => {
    expect(screen.getByText(/warming up/)).toBeInTheDocument();
  });
});

test("renders green accuracy when >= 55%", async () => {
  vi.spyOn(apiModule.api, "predictionAccuracy").mockResolvedValue({
    ...baseAccuracy,
    validated_count: 100,
    correct_count: 62,
    accuracy_pct: 62,
  });
  render(<PredictionAccuracyBadge symbolPath="BTC-USDT" timeframe="1h" />);
  await waitFor(() => {
    expect(screen.getByText(/62% \(62\/100\)/)).toBeInTheDocument();
  });
});

test("renders red accuracy when < 45%", async () => {
  vi.spyOn(apiModule.api, "predictionAccuracy").mockResolvedValue({
    ...baseAccuracy,
    validated_count: 100,
    correct_count: 38,
    accuracy_pct: 38,
  });
  render(<PredictionAccuracyBadge symbolPath="BTC-USDT" timeframe="1h" />);
  await waitFor(() => {
    expect(screen.getByText(/38% \(38\/100\)/)).toBeInTheDocument();
  });
});

test("shows pending count alongside accuracy when both exist", async () => {
  vi.spyOn(apiModule.api, "predictionAccuracy").mockResolvedValue({
    ...baseAccuracy,
    validated_count: 50,
    correct_count: 28,
    accuracy_pct: 56,
    pending_count: 7,
  });
  render(<PredictionAccuracyBadge symbolPath="BTC-USDT" timeframe="1h" />);
  await waitFor(() => {
    expect(screen.getByText(/7 pending/)).toBeInTheDocument();
  });
});
