import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import type { SignalCard as SignalCardData } from "@/lib/api";
import { BullishColumn } from "@/tabs/Tab3Scanner/BullishColumn";

function makeCard(symbol: string, full_name: string, overrides: Partial<SignalCardData> = {}): SignalCardData {
  return {
    symbol,
    full_name,
    is_favorite: false,
    points: 1,
    pct_change: 0.5,
    direction: "LONG",
    htf_direction: "LONG",
    signal_tier: "STANDARD",
    hybrid_flag: null,
    ai_score: 60,
    wyckoff_phase: "Markup",
    confidence: 70,
    scores: { smc: 1, wyckoff: 1, microstructure: 1, momentum: 1 },
    sparkline: [1, 2, 3, 4, 5],
    ts: null,
    is_stale: false,
    ...overrides,
  };
}

const cards: SignalCardData[] = [
  makeCard("BTC/USDT", "Bitcoin"),
  makeCard("ETH/USDT", "Ethereum", { signal_tier: "PAPER" }),
  makeCard("SOL/USDT", "Solana", { signal_tier: "NO_SIGNAL" }),
];

describe("BullishColumn", () => {
  test("renders heading with green color and visible count", () => {
    render(<BullishColumn cards={cards} search="" filter="all" tf="1h" />);
    const heading = screen.getByRole("heading", { name: /Bullish/i });
    expect(heading).toBeInTheDocument();
    expect(heading.textContent).toMatch(/3/);
    expect(heading.className).toMatch(/text-green/);
  });

  test("renders one SignalCard per card", () => {
    render(<BullishColumn cards={cards} search="" filter="all" tf="1h" />);
    expect(screen.getByTestId("signal-card-BTC/USDT")).toBeInTheDocument();
    expect(screen.getByTestId("signal-card-ETH/USDT")).toBeInTheDocument();
    expect(screen.getByTestId("signal-card-SOL/USDT")).toBeInTheDocument();
  });

  test("filters by search substring on symbol or full_name", () => {
    render(<BullishColumn cards={cards} search="ether" filter="all" tf="1h" />);
    expect(screen.queryByTestId("signal-card-BTC/USDT")).toBeNull();
    expect(screen.getByTestId("signal-card-ETH/USDT")).toBeInTheDocument();
    expect(screen.queryByTestId("signal-card-SOL/USDT")).toBeNull();
  });

  test("filters by tier when filter is 'confirmed'", () => {
    render(<BullishColumn cards={cards} search="" filter="confirmed" tf="1h" />);
    expect(screen.getByTestId("signal-card-BTC/USDT")).toBeInTheDocument();
    expect(screen.queryByTestId("signal-card-ETH/USDT")).toBeNull();
    expect(screen.queryByTestId("signal-card-SOL/USDT")).toBeNull();
  });

  test("filters by tier when filter is 'probable'", () => {
    render(<BullishColumn cards={cards} search="" filter="probable" tf="1h" />);
    expect(screen.queryByTestId("signal-card-BTC/USDT")).toBeNull();
    expect(screen.getByTestId("signal-card-ETH/USDT")).toBeInTheDocument();
    expect(screen.queryByTestId("signal-card-SOL/USDT")).toBeNull();
  });

  test("filters by tier when filter is 'weak'", () => {
    render(<BullishColumn cards={cards} search="" filter="weak" tf="1h" />);
    expect(screen.queryByTestId("signal-card-BTC/USDT")).toBeNull();
    expect(screen.queryByTestId("signal-card-ETH/USDT")).toBeNull();
    expect(screen.getByTestId("signal-card-SOL/USDT")).toBeInTheDocument();
  });

  test("renders empty placeholder when nothing matches", () => {
    render(<BullishColumn cards={cards} search="zzzzzz" filter="all" tf="1h" />);
    expect(screen.getByText(/No signals match/i)).toBeInTheDocument();
  });

  test("renders empty placeholder when cards is empty", () => {
    render(<BullishColumn cards={[]} search="" filter="all" tf="1h" />);
    expect(screen.getByText(/No signals match/i)).toBeInTheDocument();
  });
});
