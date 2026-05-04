import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import type { EquityCurve as EquityCurveData } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  api: { equityCurve: vi.fn() },
}));

import { api } from "@/lib/api";
import { EquityCurve } from "@/tabs/BotStatus/EquityCurve";

const sample: EquityCurveData = {
  days: 30,
  points: [
    { date: "2026-04-01T00:00:00Z", cumulative_pnl_usdt: 0 },
    { date: "2026-04-10T00:00:00Z", cumulative_pnl_usdt: 5.5 },
    { date: "2026-04-20T00:00:00Z", cumulative_pnl_usdt: -2.0 },
    { date: "2026-04-30T00:00:00Z", cumulative_pnl_usdt: 12.5 },
  ],
};

beforeEach(() => {
  vi.mocked(api.equityCurve).mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("EquityCurve", () => {
  test("renders an SVG path with the right number of segments", async () => {
    vi.mocked(api.equityCurve).mockResolvedValue(sample);
    const { container } = render(<EquityCurve />);
    await waitFor(() => {
      expect(container.querySelector("svg")).not.toBeNull();
    });
    const path = container.querySelector("path");
    expect(path).not.toBeNull();
    const d = path?.getAttribute("d") ?? "";
    // M + 3 L commands for 4 points
    expect(d).toMatch(/^M /);
    expect((d.match(/L /g) ?? []).length).toBe(3);
    // Latest cumulative shown
    expect(screen.getByText(/\+\$12\.50/)).toBeInTheDocument();
  });

  test("shows empty state when no points", async () => {
    vi.mocked(api.equityCurve).mockResolvedValue({ days: 30, points: [] });
    render(<EquityCurve />);
    await waitFor(() => {
      expect(screen.getByText(/no trades yet/i)).toBeInTheDocument();
    });
  });
});
