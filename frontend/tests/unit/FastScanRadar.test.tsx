import { render, screen, waitFor } from "@testing-library/react";
import { vi, afterEach, test, expect } from "vitest";
import { FastScanRadar } from "@/tabs/Tab3Scanner/FastScanRadar";
import * as apiModule from "@/lib/api";

const baseRadar = {
  timeframe: "1h",
  cache_size: 0,
  bullish: [],
  bearish: [],
  by_tier: {},
};

afterEach(() => {
  vi.restoreAllMocks();
});

test("renders 'warming up' message when cache is empty", async () => {
  vi.spyOn(apiModule.api, "scannerFast").mockResolvedValue(baseRadar);
  render(<FastScanRadar />);
  await waitFor(() => {
    expect(screen.getByText(/warming up/i)).toBeInTheDocument();
  });
});

test("renders bullish cards with symbol + score", async () => {
  vi.spyOn(apiModule.api, "scannerFast").mockResolvedValue({
    ...baseRadar,
    cache_size: 2,
    bullish: [
      {
        symbol: "BTC/USDT",
        timeframe: "1h",
        final_score: 65,
        direction: "LONG",
        confidence: 0.85,
        tier: "confirmed",
        phase: "markup",
        modules: [
          { name: "trend", score: 70, weight: 0.3, notes: "" },
          { name: "momentum", score: 60, weight: 0.3, notes: "" },
          { name: "structure", score: 50, weight: 0.2, notes: "" },
          { name: "volume", score: 40, weight: 0.1, notes: "" },
          { name: "volatility", score: 20, weight: 0.1, notes: "" },
        ],
        last_close: 81000,
        expected_move_pct: 3.5,
        rr_estimate: 2.0,
        scanned_at: "2026-05-12T15:00:00Z",
      },
    ],
    bearish: [],
    by_tier: { confirmed: 1 },
  });
  render(<FastScanRadar />);
  await waitFor(() => {
    expect(screen.getByText("BTC/USDT")).toBeInTheDocument();
  });
  expect(screen.getByText("+65")).toBeInTheDocument();
  expect(screen.getByText(/conf 85%/)).toBeInTheDocument();
});

test("renders error banner when API throws", async () => {
  vi.spyOn(apiModule.api, "scannerFast").mockRejectedValue(new Error("network down"));
  render(<FastScanRadar />);
  await waitFor(() => {
    expect(screen.getByText(/network down/)).toBeInTheDocument();
  });
});

test("tier filter buttons are rendered", async () => {
  vi.spyOn(apiModule.api, "scannerFast").mockResolvedValue({
    ...baseRadar,
    cache_size: 5,
    by_tier: { confirmed: 2, probable: 3 },
  });
  render(<FastScanRadar />);
  await waitFor(() => {
    expect(screen.getByText(/All/)).toBeInTheDocument();
  });
  expect(screen.getByText(/Confirmed/)).toBeInTheDocument();
  expect(screen.getByText(/Probable/)).toBeInTheDocument();
});

test("shows bearish cards in their own column", async () => {
  vi.spyOn(apiModule.api, "scannerFast").mockResolvedValue({
    ...baseRadar,
    cache_size: 1,
    bullish: [],
    bearish: [
      {
        symbol: "ETH/USDT",
        timeframe: "1h",
        final_score: -42,
        direction: "SHORT",
        confidence: 0.65,
        tier: "probable",
        phase: "markdown",
        modules: [
          { name: "trend", score: -50, weight: 0.3, notes: "" },
          { name: "momentum", score: -40, weight: 0.3, notes: "" },
          { name: "structure", score: -30, weight: 0.2, notes: "" },
          { name: "volume", score: -20, weight: 0.1, notes: "" },
          { name: "volatility", score: -10, weight: 0.1, notes: "" },
        ],
        last_close: 2400,
        expected_move_pct: -2.1,
        rr_estimate: 2.0,
        scanned_at: "2026-05-12T15:00:00Z",
      },
    ],
    by_tier: { probable: 1 },
  });
  render(<FastScanRadar />);
  await waitFor(() => {
    expect(screen.getByText("ETH/USDT")).toBeInTheDocument();
  });
  expect(screen.getByText("-42")).toBeInTheDocument();
});
