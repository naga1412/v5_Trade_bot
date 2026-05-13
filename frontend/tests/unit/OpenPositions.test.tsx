import { render, screen, waitFor, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import type { OpenPosition } from "@/lib/api";
import type { ShadowUpdatesState } from "@/hooks/useShadowUpdates";

vi.mock("@/lib/api", () => ({
  api: { openPositions: vi.fn() },
}));

let shadowState: ShadowUpdatesState = {
  lastOpened: null,
  lastClosed: null,
  lastPnlTick: {},
  lastUniverseRefresh: null,
};

vi.mock("@/hooks/useShadowUpdates", () => ({
  useShadowUpdates: () => shadowState,
}));

import { api } from "@/lib/api";
import { OpenPositions } from "@/tabs/BotStatus/OpenPositions";

const samplePos: OpenPosition[] = [
  {
    symbol: "BTC/USDT",
    direction: "LONG",
    entry_price: 100,
    stop_loss: 95,
    take_profit: 110,
    position_size_usdt: 1000,
    bars_held: 4,
    opened_at: "2026-05-03T08:00:00Z",
    signal_id: "sig-btc",
    current_price: 105,
    unrealized_pnl_pct: 5,
    unrealized_pnl_usdt: 50,
  },
  {
    symbol: "ETH/USDT",
    direction: "SHORT",
    entry_price: 200,
    stop_loss: 210,
    take_profit: 180,
    position_size_usdt: 500,
    bars_held: 2,
    opened_at: "2026-05-03T10:00:00Z",
    signal_id: "sig-eth",
    current_price: 195,
    unrealized_pnl_pct: 2.5,
    unrealized_pnl_usdt: 12.5,
  },
];

beforeEach(() => {
  vi.mocked(api.openPositions).mockReset();
  shadowState = {
    lastOpened: null,
    lastClosed: null,
    lastPnlTick: {},
    lastUniverseRefresh: null,
  };
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("OpenPositions", () => {
  test("shows 'no open positions' when list is empty", async () => {
    vi.mocked(api.openPositions).mockResolvedValue([]);
    render(<OpenPositions />);
    await waitFor(() => {
      expect(screen.getByText(/no open positions/i)).toBeInTheDocument();
    });
  });

  test("renders one card per position with direction arrow", async () => {
    vi.mocked(api.openPositions).mockResolvedValue(samplePos);
    render(<OpenPositions />);
    await waitFor(() => {
      expect(screen.getByText("BTC/USDT")).toBeInTheDocument();
    });
    expect(screen.getByText("ETH/USDT")).toBeInTheDocument();
    // long arrow + short arrow
    expect(screen.getByText("↗")).toBeInTheDocument();
    expect(screen.getByText("↘")).toBeInTheDocument();
  });

  test("pnl tick updates a card's current price + pct in place", async () => {
    vi.mocked(api.openPositions).mockResolvedValue(samplePos);
    const { rerender } = render(<OpenPositions />);
    await waitFor(() => {
      expect(screen.getByText("BTC/USDT")).toBeInTheDocument();
    });
    // Now update shadow state with a new pnl tick for BTC
    act(() => {
      shadowState = {
        ...shadowState,
        lastPnlTick: {
          "BTC/USDT": {
            type: "shadow_pnl_tick",
            symbol: "BTC/USDT",
            current_price: 108,
            unrealized_pnl_pct: 8,
          },
        },
      };
    });
    rerender(<OpenPositions />);
    // Match the exact formatted price ("108.00") rather than `/108/` —
    // the latter also matches the "held Xh Ym" duration cell when the
    // wall-clock makes Xh land in the 108–109 range (real failure observed
    // 2026-05-07 ~20:44 UTC against opened_at 2026-05-03T08:00:00Z).
    await waitFor(() => {
      expect(screen.getByText("108.00")).toBeInTheDocument();
    });
  });

  test("clicking a position card navigates to live-prediction with that symbol", async () => {
    const { fireEvent } = await import("@testing-library/react");
    window.location.hash = "";
    vi.mocked(api.openPositions).mockResolvedValue(samplePos);
    render(<OpenPositions />);
    await waitFor(() => {
      expect(screen.getByText("BTC/USDT")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByLabelText(/open BTC\/USDT LONG chart/i));
    expect(window.location.hash).toBe(
      "#/live-prediction?symbol=BTC%2FUSDT&tf=1h",
    );
    window.location.hash = "";
  });

  test("opened event triggers refetch", async () => {
    vi.mocked(api.openPositions).mockResolvedValue(samplePos);
    const { rerender } = render(<OpenPositions />);
    await waitFor(() => {
      expect(api.openPositions).toHaveBeenCalledTimes(1);
    });
    act(() => {
      shadowState = {
        ...shadowState,
        lastOpened: {
          type: "shadow_position_opened",
          symbol: "SOL/USDT",
          direction: "LONG",
          entry_price: 50,
          sl: 48,
          tp: 55,
          signal_id: "sig-sol",
          score: 0.8,
          confidence: 0.7,
          opened_at: "2026-05-03T11:00:00Z",
        },
      };
    });
    rerender(<OpenPositions />);
    await waitFor(() => {
      expect(api.openPositions).toHaveBeenCalledTimes(2);
    });
  });
});
