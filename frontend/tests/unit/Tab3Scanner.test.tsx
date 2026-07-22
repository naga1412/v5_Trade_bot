import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import type { ScannerRadar } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  api: { scannerRadar: vi.fn() },
}));

import { api } from "@/lib/api";
import { Tab3Scanner } from "@/tabs/Tab3Scanner";

const sample: ScannerRadar = {
  scanned_at: "2026-05-03T00:00:00Z",
  market: "crypto",
  timeframe: "1h",
  scanned_count: 200,
  filter_counts: { all: 200, confirmed: 5, probable: 12, weak: 180, diverging: 3 },
  supervisor_progress: { done: 4, total: 8 },
  bullish: [
    {
      symbol: "BTC/USDT",
      full_name: "Bitcoin",
      is_favorite: true,
      points: 12,
      pct_change: 2.5,
      direction: "LONG",
      htf_direction: "LONG",
      signal_tier: "STANDARD",
      hybrid_flag: null,
      ai_score: 78,
      wyckoff_phase: "Markup",
      confidence: 80,
      scores: { smc: 5, wyckoff: 5, microstructure: 5, momentum: 5 },
      sparkline: [1, 2, 3, 4, 5, 6, 7],
      ts: null,
      is_stale: false,
    },
  ],
  bearish: [
    {
      symbol: "DOGE/USDT",
      full_name: "Dogecoin",
      is_favorite: false,
      points: -3,
      pct_change: -1.2,
      direction: "SHORT",
      htf_direction: "SHORT",
      signal_tier: "PAPER",
      hybrid_flag: null,
      ai_score: -42,
      wyckoff_phase: "Distribution",
      confidence: 65,
      scores: { smc: -2, wyckoff: -2, microstructure: -2, momentum: -2 },
      sparkline: [7, 6, 5, 4, 3, 2, 1],
      ts: null,
      is_stale: false,
    },
  ],
};

beforeEach(() => {
  vi.mocked(api.scannerRadar).mockReset();
  vi.mocked(api.scannerRadar).mockResolvedValue(sample);
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("Tab3Scanner", () => {
  test("renders toolbar + supervisor bar + 2 columns + footer", async () => {
    render(<Tab3Scanner />);
    await waitFor(() => {
      expect(api.scannerRadar).toHaveBeenCalled();
    });
    expect(screen.getByRole("toolbar", { name: /scanner toolbar/i })).toBeInTheDocument();
    expect(screen.getByText(/Hybrid Supervisor/i)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Bullish/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Bearish/i })).toBeInTheDocument();
    expect(screen.getByText(/Click card to view chart/i)).toBeInTheDocument();
  });

  test("footer shows scanned count + tf + refresh interval", async () => {
    render(<Tab3Scanner />);
    await waitFor(() => {
      expect(screen.getByText(/Scanning 200 crypto • 1h/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/every 2 min/i)).toBeInTheDocument();
  });

  test("renders bullish + bearish cards from API data", async () => {
    render(<Tab3Scanner />);
    await waitFor(() => {
      expect(screen.getByTestId("signal-card-BTC/USDT")).toBeInTheDocument();
    });
    expect(screen.getByTestId("signal-card-DOGE/USDT")).toBeInTheDocument();
  });

  test("renders empty placeholders when both sides are empty", async () => {
    vi.mocked(api.scannerRadar).mockResolvedValueOnce({
      ...sample,
      bullish: [],
      bearish: [],
      scanned_count: 0,
    });
    render(<Tab3Scanner />);
    await waitFor(() => {
      const empties = screen.getAllByText(/No signals match/i);
      expect(empties.length).toBeGreaterThanOrEqual(2);
    });
  });

  test("clicking refresh re-invokes scannerRadar", async () => {
    render(<Tab3Scanner />);
    await waitFor(() => {
      expect(api.scannerRadar).toHaveBeenCalledTimes(1);
    });
    fireEvent.click(screen.getByRole("button", { name: /Refresh/i }));
    await waitFor(() => {
      expect(api.scannerRadar).toHaveBeenCalledTimes(2);
    });
  });

  test("renders error banner when API rejects", async () => {
    vi.mocked(api.scannerRadar).mockRejectedValueOnce(new Error("server down"));
    render(<Tab3Scanner />);
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/server down/i);
    });
  });
});
