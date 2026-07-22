import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import type { SignalCard as SignalCardData } from "@/lib/api";
import { BearishColumn } from "@/tabs/Tab3Scanner/BearishColumn";

function makeCard(symbol: string, full_name: string, overrides: Partial<SignalCardData> = {}): SignalCardData {
  return {
    symbol,
    full_name,
    is_favorite: false,
    points: -1,
    pct_change: -0.5,
    direction: "SHORT",
    htf_direction: "SHORT",
    signal_tier: "STANDARD",
    hybrid_flag: null,
    ai_score: -60,
    wyckoff_phase: "Distribution",
    confidence: 70,
    scores: { smc: -1, wyckoff: -1, microstructure: -1, momentum: -1 },
    sparkline: [5, 4, 3, 2, 1],
    ts: null,
    is_stale: false,
    ...overrides,
  };
}

const cards: SignalCardData[] = [
  makeCard("DOGE/USDT", "Dogecoin"),
  makeCard("XRP/USDT", "Ripple", { signal_tier: "SMALL" }),
];

describe("BearishColumn", () => {
  test("renders heading with red color and visible count", () => {
    render(<BearishColumn cards={cards} search="" filter="all" tf="1h" />);
    const heading = screen.getByRole("heading", { name: /Bearish/i });
    expect(heading).toBeInTheDocument();
    expect(heading.textContent).toMatch(/2/);
    expect(heading.className).toMatch(/text-red/);
  });

  test("renders one SignalCard per card", () => {
    render(<BearishColumn cards={cards} search="" filter="all" tf="1h" />);
    expect(screen.getByTestId("signal-card-DOGE/USDT")).toBeInTheDocument();
    expect(screen.getByTestId("signal-card-XRP/USDT")).toBeInTheDocument();
  });

  test("renders empty placeholder when nothing matches", () => {
    render(<BearishColumn cards={[]} search="" filter="all" tf="1h" />);
    expect(screen.getByText(/No signals match/i)).toBeInTheDocument();
  });
});
