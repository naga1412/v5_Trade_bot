import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import type { PromotionGate as PromotionGateData } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  api: { promotionGate: vi.fn() },
}));

import { api } from "@/lib/api";
import { PromotionGate } from "@/tabs/BotStatus/PromotionGate";

beforeEach(() => {
  vi.mocked(api.promotionGate).mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

const allPassing: PromotionGateData = {
  target_mode: "telegram-approve",
  metrics: [
    { name: "win_rate", current: 0.6, threshold: 0.5, operator: ">=", passing: true },
    { name: "trades", current: 100, threshold: 50, operator: ">=", passing: true },
    { name: "max_drawdown", current: 0.05, threshold: 0.10, operator: "<=", passing: true },
  ],
  all_passing: true,
  distance_summary: "All metrics passing.",
};

const partial: PromotionGateData = {
  target_mode: "telegram-approve",
  metrics: [
    { name: "win_rate", current: 0.45, threshold: 0.5, operator: ">=", passing: false },
    { name: "trades", current: 100, threshold: 50, operator: ">=", passing: true },
    { name: "max_drawdown", current: 0.12, threshold: 0.10, operator: "<=", passing: false },
  ],
  all_passing: false,
  distance_summary: "Win rate 5pp below; DD 2pp above.",
};

const allFailing: PromotionGateData = {
  target_mode: "fully-auto",
  metrics: [
    { name: "win_rate", current: 0.30, threshold: 0.5, operator: ">=", passing: false },
  ],
  all_passing: false,
  distance_summary: "All failing.",
};

describe("PromotionGate", () => {
  test("renders all-passing metrics with checkmarks", async () => {
    vi.mocked(api.promotionGate).mockResolvedValue(allPassing);
    render(<PromotionGate />);
    await waitFor(() => {
      expect(screen.getAllByLabelText(/passing/).length).toBe(3);
    });
    expect(screen.getByText("All metrics passing.")).toBeInTheDocument();
    expect(screen.getByText(/telegram-approve/i)).toBeInTheDocument();
  });

  test("renders partial state with progress bars for failing metrics", async () => {
    vi.mocked(api.promotionGate).mockResolvedValue(partial);
    const { container } = render(<PromotionGate />);
    await waitFor(() => {
      // 1 passing checkmark + 2 progress bars
      expect(screen.getAllByLabelText(/passing/).length).toBe(1);
    });
    // Progress bars render as divs with role="progressbar"
    expect(container.querySelectorAll("[role=progressbar]").length).toBe(2);
    expect(screen.getByText(/Win rate 5pp below/)).toBeInTheDocument();
  });

  test("renders all-failing state without crash", async () => {
    vi.mocked(api.promotionGate).mockResolvedValue(allFailing);
    const { container } = render(<PromotionGate />);
    await waitFor(() => {
      expect(container.querySelectorAll("[role=progressbar]").length).toBe(1);
    });
    expect(screen.getByText(/fully-auto/i)).toBeInTheDocument();
  });
});
