import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import type { BotOverview } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  api: { botOverview: vi.fn() },
}));

vi.mock("@/hooks/useShadowUpdates", () => ({
  useShadowUpdates: () => ({
    lastOpened: null,
    lastClosed: null,
    lastPnlTick: {},
    lastUniverseRefresh: null,
  }),
}));

import { api } from "@/lib/api";
import { OverviewStats } from "@/tabs/BotStatus/OverviewStats";

const sample: BotOverview = {
  last_24h: {
    window: "24h", trades: 5, pnl_usdt: 8.4, pnl_pct: 0.84,
    win_rate: 0.6, sharpe_annualized: 1.2, max_drawdown: 0.05, profit_factor: 1.7,
  },
  last_7d: {
    window: "7d", trades: 25, pnl_usdt: 42.1, pnl_pct: 4.2,
    win_rate: 0.52, sharpe_annualized: 1.1, max_drawdown: 0.07, profit_factor: 1.5,
  },
  last_30d: {
    window: "30d", trades: 150, pnl_usdt: 210.0, pnl_pct: 21.0,
    win_rate: 0.47, sharpe_annualized: 1.0, max_drawdown: 0.12, profit_factor: 1.4,
  },
  long_only_30d: {
    window: "30d", trades: 80, pnl_usdt: 130, pnl_pct: 13,
    win_rate: 0.49, sharpe_annualized: 1.0, max_drawdown: 0.08, profit_factor: 1.5,
  },
  short_only_30d: {
    window: "30d", trades: 70, pnl_usdt: 80, pnl_pct: 8,
    win_rate: 0.44, sharpe_annualized: 0.9, max_drawdown: 0.10, profit_factor: 1.3,
  },
};

beforeEach(() => {
  vi.mocked(api.botOverview).mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("OverviewStats", () => {
  test("shows dashes when loading (no data yet)", () => {
    vi.mocked(api.botOverview).mockReturnValue(new Promise(() => { /* never resolves */ }));
    render(<OverviewStats />);
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  test("renders P&L, win rate breakdown, and aggregate stats from data", async () => {
    vi.mocked(api.botOverview).mockResolvedValue(sample);
    render(<OverviewStats />);
    // 24h positive P&L formatted with $
    await waitFor(() => {
      expect(screen.getByText(/\+\$8\.40/)).toBeInTheDocument();
    });
    // 30d trades count + win rate (combined in one span)
    expect(screen.getByText("47% (71/150)")).toBeInTheDocument();
    // Long / Short subline (single text node)
    expect(screen.getByText("Long: 49% Short: 44%")).toBeInTheDocument();
  });

  test("renders alert panel when fetch fails", async () => {
    vi.mocked(api.botOverview).mockRejectedValue(new Error("network down"));
    const { container } = render(<OverviewStats />);
    await waitFor(() => {
      expect(container.querySelector(".border-red\\/60")).not.toBeNull();
    });
  });
});
