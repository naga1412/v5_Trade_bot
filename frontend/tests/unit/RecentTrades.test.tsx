import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import type { RecentTrade } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  api: { recentTrades: vi.fn() },
}));

import { api } from "@/lib/api";
import { RecentTrades } from "@/tabs/BotStatus/RecentTrades";

const sample: RecentTrade[] = [
  {
    closed_at: "2026-05-03T08:00:00Z",
    symbol: "BTC/USDT",
    direction: "LONG",
    entry_price: 100,
    exit_price: 110,
    pnl_pct: 10,
    pnl_usdt: 100,
    exit_reason: "TAKE_PROFIT",
    bars_held: 8,
    signal_id: "sig-btc-1",
  },
  {
    closed_at: "2026-05-02T14:00:00Z",
    symbol: "ETH/USDT",
    direction: "SHORT",
    entry_price: 200,
    exit_price: 210,
    pnl_pct: -5,
    pnl_usdt: -50,
    exit_reason: "STOP_LOSS",
    bars_held: 4,
    signal_id: "sig-eth-1",
  },
];

beforeEach(() => {
  vi.mocked(api.recentTrades).mockReset();
  vi.mocked(api.recentTrades).mockResolvedValue(sample);
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("RecentTrades", () => {
  test("renders one row per recent trade", async () => {
    render(<RecentTrades />);
    await waitFor(() => {
      expect(screen.getByText("BTC/USDT")).toBeInTheDocument();
    });
    expect(screen.getByText("ETH/USDT")).toBeInTheDocument();
    expect(screen.getByText(/TP/i)).toBeInTheDocument();
    expect(screen.getByText(/SL/i)).toBeInTheDocument();
  });

  test("filter dropdowns trigger refetch with the filter args", async () => {
    render(<RecentTrades />);
    await waitFor(() => {
      expect(api.recentTrades).toHaveBeenCalledTimes(1);
    });
    // Last call default has limit 100
    expect(vi.mocked(api.recentTrades).mock.calls[0]?.[0]).toMatchObject({ limit: 100 });

    const dirSelect = screen.getByLabelText(/direction/i) as HTMLSelectElement;
    fireEvent.change(dirSelect, { target: { value: "LONG" } });
    await waitFor(() => {
      expect(api.recentTrades).toHaveBeenCalledTimes(2);
    });
    const lastCall = vi.mocked(api.recentTrades).mock.calls.at(-1)?.[0];
    expect(lastCall).toMatchObject({ direction: "LONG", limit: 100 });
  });

  test("formats pnl with sign and percent", async () => {
    render(<RecentTrades />);
    await waitFor(() => {
      expect(screen.getByText(/\+\$100\.00/)).toBeInTheDocument();
    });
    expect(screen.getByText(/-\$50\.00/)).toBeInTheDocument();
  });

  test("signal links use the live-prediction deeplink", async () => {
    render(<RecentTrades />);
    await waitFor(() => {
      expect(screen.getByText("BTC/USDT")).toBeInTheDocument();
    });
    const links = screen.getAllByRole("link");
    const btcLink = links.find((l) => l.getAttribute("href")?.includes("sig-btc-1"));
    expect(btcLink).toBeDefined();
    expect(btcLink?.getAttribute("href")).toContain("#/live-prediction?signal=sig-btc-1");
  });
});
