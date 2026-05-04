import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import type { LongShortBreakdown as LongShortData } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  api: { longShort: vi.fn() },
}));

import { api } from "@/lib/api";
import { LongShortBreakdown } from "@/tabs/BotStatus/LongShortBreakdown";

const sample: LongShortData = {
  long: {
    window: "30d", trades: 80, pnl_usdt: 130.5, pnl_pct: 13,
    win_rate: 0.55, sharpe_annualized: 1.0, max_drawdown: 0.07, profit_factor: 1.6,
  },
  short: {
    window: "30d", trades: 70, pnl_usdt: -10.0, pnl_pct: -1,
    win_rate: 0.40, sharpe_annualized: -0.2, max_drawdown: 0.10, profit_factor: 0.9,
  },
};

beforeEach(() => {
  vi.mocked(api.longShort).mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("LongShortBreakdown", () => {
  test("renders LONG and SHORT cards with stats", async () => {
    vi.mocked(api.longShort).mockResolvedValue(sample);
    render(<LongShortBreakdown />);
    await waitFor(() => {
      expect(screen.getByText("+$130.50")).toBeInTheDocument();
    });
    expect(screen.getByText("LONG")).toBeInTheDocument();
    expect(screen.getByText("SHORT")).toBeInTheDocument();
    expect(screen.getByText("80")).toBeInTheDocument();
    expect(screen.getByText("70")).toBeInTheDocument();
    expect(screen.getByText("-$10.00")).toBeInTheDocument();
  });

  test("shows dashes when loading", () => {
    vi.mocked(api.longShort).mockReturnValue(new Promise(() => { /* never */ }));
    render(<LongShortBreakdown />);
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });
});
