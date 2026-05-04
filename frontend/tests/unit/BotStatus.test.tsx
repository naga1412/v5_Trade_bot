import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  api: {
    botOverview: vi.fn(),
    promotionGate: vi.fn(),
    openPositions: vi.fn(),
    perAssetStats: vi.fn(),
    longShort: vi.fn(),
    equityCurve: vi.fn(),
    recentTrades: vi.fn(),
  },
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
import { BotStatus } from "@/tabs/BotStatus";

beforeEach(() => {
  vi.mocked(api.botOverview).mockResolvedValue({
    last_24h: { window: "24h", trades: 0, pnl_usdt: 0, pnl_pct: 0, win_rate: 0, sharpe_annualized: 0, max_drawdown: 0, profit_factor: 0 },
    last_7d: { window: "7d", trades: 0, pnl_usdt: 0, pnl_pct: 0, win_rate: 0, sharpe_annualized: 0, max_drawdown: 0, profit_factor: 0 },
    last_30d: { window: "30d", trades: 0, pnl_usdt: 0, pnl_pct: 0, win_rate: 0, sharpe_annualized: 0, max_drawdown: 0, profit_factor: 0 },
    long_only_30d: { window: "30d", trades: 0, pnl_usdt: 0, pnl_pct: 0, win_rate: 0, sharpe_annualized: 0, max_drawdown: 0, profit_factor: 0 },
    short_only_30d: { window: "30d", trades: 0, pnl_usdt: 0, pnl_pct: 0, win_rate: 0, sharpe_annualized: 0, max_drawdown: 0, profit_factor: 0 },
  });
  vi.mocked(api.promotionGate).mockResolvedValue({
    target_mode: "telegram-approve",
    metrics: [],
    all_passing: true,
    distance_summary: "ok",
  });
  vi.mocked(api.openPositions).mockResolvedValue([]);
  vi.mocked(api.perAssetStats).mockResolvedValue([]);
  vi.mocked(api.longShort).mockResolvedValue({
    long: { window: "30d", trades: 0, pnl_usdt: 0, pnl_pct: 0, win_rate: 0, sharpe_annualized: 0, max_drawdown: 0, profit_factor: 0 },
    short: { window: "30d", trades: 0, pnl_usdt: 0, pnl_pct: 0, win_rate: 0, sharpe_annualized: 0, max_drawdown: 0, profit_factor: 0 },
  });
  vi.mocked(api.equityCurve).mockResolvedValue({ days: 30, points: [] });
  vi.mocked(api.recentTrades).mockResolvedValue([]);
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("BotStatus tab page", () => {
  test("renders all 7 sections by title", async () => {
    render(<BotStatus />);
    await waitFor(() => {
      expect(screen.getByText(/overview/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/promotion gate/i)).toBeInTheDocument();
    // "Open Positions" appears as both title (h3) and empty-state body text
    expect(screen.getAllByText(/open positions/i).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/per-asset stats/i)).toBeInTheDocument();
    expect(screen.getByText(/long vs short/i)).toBeInTheDocument();
    expect(screen.getByText(/equity curve/i)).toBeInTheDocument();
    expect(screen.getByText(/recent trades/i)).toBeInTheDocument();
  });

  test("uses a vertical-stack container suitable for mobile", () => {
    const { container } = render(<BotStatus />);
    const root = container.firstChild as HTMLElement | null;
    expect(root?.className).toContain("overflow-y-auto");
  });
});
