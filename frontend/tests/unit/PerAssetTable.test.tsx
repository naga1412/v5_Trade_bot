import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import type { PerAssetStat } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  api: { perAssetStats: vi.fn() },
}));

import { api } from "@/lib/api";
import { PerAssetTable } from "@/tabs/BotStatus/PerAssetTable";

const COMPUTED_AT = "2026-05-03T12:00:00Z";
const LAST_TRADE = "2026-05-03T11:30:00Z";

const sample: PerAssetStat[] = [
  { symbol: "BTC/USDT", trades: 10, win_rate: 0.6, avg_rr: 2.0, pnl_usdt: 25.5, sharpe_annualized: 1.2, computed_at: COMPUTED_AT, last_trade_closed_at: LAST_TRADE },
  { symbol: "ETH/USDT", trades: 8, win_rate: 0.50, avg_rr: 1.8, pnl_usdt: -3.5, sharpe_annualized: -0.2, computed_at: COMPUTED_AT, last_trade_closed_at: LAST_TRADE },
  { symbol: "SOL/USDT", trades: 5, win_rate: 0.40, avg_rr: 1.5, pnl_usdt: 12.0, sharpe_annualized: 0.8, computed_at: COMPUTED_AT, last_trade_closed_at: LAST_TRADE },
];

beforeEach(() => {
  vi.mocked(api.perAssetStats).mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("PerAssetTable", () => {
  test("renders one row per asset (default sort by P&L desc)", async () => {
    vi.mocked(api.perAssetStats).mockResolvedValue(sample);
    render(<PerAssetTable />);
    await waitFor(() => {
      expect(screen.getAllByText(/BTC\/USDT|ETH\/USDT|SOL\/USDT/)).toHaveLength(3);
    });
    // P&L desc default → BTC (25.5), SOL (12), ETH (-3.5)
    const rows = screen.getAllByRole("row");
    // [0] is header
    expect(rows[1]?.textContent).toContain("BTC/USDT");
    expect(rows[2]?.textContent).toContain("SOL/USDT");
    expect(rows[3]?.textContent).toContain("ETH/USDT");
  });

  test("clicking column header re-sorts (Trades desc → asc)", async () => {
    vi.mocked(api.perAssetStats).mockResolvedValue(sample);
    render(<PerAssetTable />);
    await waitFor(() => {
      expect(screen.getByText("BTC/USDT")).toBeInTheDocument();
    });
    const tradesHeader = screen.getByRole("columnheader", { name: /trades/i });
    fireEvent.click(tradesHeader); // first click: trades desc
    let rows = screen.getAllByRole("row");
    expect(rows[1]?.textContent).toContain("BTC/USDT"); // 10
    expect(rows[2]?.textContent).toContain("ETH/USDT"); // 8
    expect(rows[3]?.textContent).toContain("SOL/USDT"); // 5
    fireEvent.click(tradesHeader); // second click: trades asc
    rows = screen.getAllByRole("row");
    expect(rows[1]?.textContent).toContain("SOL/USDT"); // 5
  });

  test("formats win rate as percent and P&L with sign", async () => {
    vi.mocked(api.perAssetStats).mockResolvedValue(sample);
    render(<PerAssetTable />);
    await waitFor(() => {
      expect(screen.getByText("60%")).toBeInTheDocument();
    });
    expect(screen.getByText("+$25.50")).toBeInTheDocument();
    expect(screen.getByText("-$3.50")).toBeInTheDocument();
  });

  test("PR10.6: renders per-row 'Last trade' relative-time stamps", async () => {
    // 3 rows with DIFFERENT last_trade_closed_at values so we can prove
    // each row shows its own per-symbol timestamp (not just sorted[0]'s).
    const now = new Date("2026-05-03T12:00:00Z").getTime();
    vi.setSystemTime(now);
    const mixed: PerAssetStat[] = [
      // 3 minutes ago
      { symbol: "BTC/USDT", trades: 10, win_rate: 0.6, avg_rr: 2.0, pnl_usdt: 25.5,
        sharpe_annualized: 1.2, computed_at: "2026-05-03T12:00:00Z",
        last_trade_closed_at: "2026-05-03T11:57:00Z" },
      // 2 hours ago
      { symbol: "ETH/USDT", trades: 8, win_rate: 0.50, avg_rr: 1.8, pnl_usdt: -3.5,
        sharpe_annualized: -0.2, computed_at: "2026-05-03T12:00:00Z",
        last_trade_closed_at: "2026-05-03T10:00:00Z" },
      // never traded → "—"
      { symbol: "SOL/USDT", trades: 0, win_rate: 0, avg_rr: 0, pnl_usdt: 0,
        sharpe_annualized: null, computed_at: "2026-05-03T12:00:00Z",
        last_trade_closed_at: null },
    ];
    vi.mocked(api.perAssetStats).mockResolvedValue(mixed);
    render(<PerAssetTable />);
    await waitFor(() => {
      expect(screen.getByText("BTC/USDT")).toBeInTheDocument();
    });

    // Each row shows ITS OWN per-symbol relative-time stamp.
    // Default sort is P&L desc → BTC (25.5), SOL (0), ETH (-3.5)
    const rows = screen.getAllByRole("row");
    expect(rows[1]?.textContent).toContain("BTC/USDT");
    expect(rows[1]?.textContent).toContain("3m ago");
    expect(rows[2]?.textContent).toContain("SOL/USDT");
    expect(rows[2]?.textContent).toContain("—");
    expect(rows[3]?.textContent).toContain("ETH/USDT");
    expect(rows[3]?.textContent).toContain("2h ago");

    vi.useRealTimers();
  });
});
