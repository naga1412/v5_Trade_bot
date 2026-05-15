// Click-to-chart: Fast Scan card → Tab 1 deeplink.
//
// Mirrors SignalCardNavigation.test.tsx but for the FastScanRadar
// component on the Scanner tab. Clicking a bullish or bearish card
// must set window.location.hash to the live-prediction route with
// ?symbol=…&tf=…, so that App.tsx's query-sync effect picks up the
// chosen symbol and renders the chart for it.

import { render, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import type { FastScanRadar as FastScanRadarData } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  api: { scannerFast: vi.fn() },
}));

import { api } from "@/lib/api";
import { FastScanRadar } from "@/tabs/Tab3Scanner/FastScanRadar";

const sample: FastScanRadarData = {
  cache_size: 2,
  timeframe: "1h",
  by_tier: {},
  bullish: [
    {
      symbol: "DOGEUSDT",
      timeframe: "1h",
      direction: "LONG",
      tier: "probable",
      confidence: 0.55,
      final_score: 35,
      expected_move_pct: 3.6,
      rr_estimate: 2.0,
      last_close: 0.11,
      phase: "markup",
      modules: [],
      scanned_at: "2026-05-13T22:00:00Z",
    },
  ],
  bearish: [
    {
      symbol: "ZECUSDT",
      timeframe: "1h",
      direction: "SHORT",
      tier: "confirmed",
      confidence: 1.0,
      final_score: -79,
      expected_move_pct: -6.0,
      rr_estimate: 2.0,
      last_close: 521.42,
      phase: "markdown",
      modules: [],
      scanned_at: "2026-05-13T22:00:00Z",
    },
  ],
};

beforeEach(() => {
  vi.mocked(api.scannerFast).mockReset();
  window.location.hash = "";
});

afterEach(() => {
  vi.clearAllMocks();
  window.location.hash = "";
});

describe("FastScanRadar navigation", () => {
  test("clicking a bullish card sets the live-prediction hash with the active tf", async () => {
    vi.mocked(api.scannerFast).mockResolvedValue(sample);
    render(<FastScanRadar timeframe="1h" refreshIntervalMs={999_999} />);
    await waitFor(() => {
      expect(screen.getByText("DOGEUSDT")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByLabelText(/scan card DOGEUSDT/i));
    expect(window.location.hash).toBe(
      "#/live-prediction?symbol=DOGEUSDT&tf=1h",
    );
  });

  test("clicking a bearish card uses the active timeframe prop", async () => {
    vi.mocked(api.scannerFast).mockResolvedValue(sample);
    render(<FastScanRadar timeframe="4h" refreshIntervalMs={999_999} />);
    await waitFor(() => {
      expect(screen.getByText("ZECUSDT")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByLabelText(/scan card ZECUSDT/i));
    expect(window.location.hash).toBe(
      "#/live-prediction?symbol=ZECUSDT&tf=4h",
    );
  });

  test("keyboard Enter on a card also navigates", async () => {
    vi.mocked(api.scannerFast).mockResolvedValue(sample);
    render(<FastScanRadar timeframe="1h" refreshIntervalMs={999_999} />);
    await waitFor(() => {
      expect(screen.getByText("DOGEUSDT")).toBeInTheDocument();
    });
    fireEvent.keyDown(screen.getByLabelText(/scan card DOGEUSDT/i), {
      key: "Enter",
    });
    expect(window.location.hash).toBe(
      "#/live-prediction?symbol=DOGEUSDT&tf=1h",
    );
  });
});
