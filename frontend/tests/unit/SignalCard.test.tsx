import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import type { SignalCard as SignalCardData } from "@/lib/api";
import { SignalCard } from "@/tabs/Tab3Scanner/SignalCard";

function makeCard(overrides: Partial<SignalCardData> = {}): SignalCardData {
  return {
    symbol: "BTC/USDT",
    full_name: "Bitcoin",
    is_favorite: true,
    points: 12,
    pct_change: 3.42,
    direction: "LONG",
    htf_direction: "LONG",
    signal_tier: "STANDARD",
    hybrid_flag: "LONG",
    ai_score: 78,
    wyckoff_phase: "Markup",
    confidence: 84,
    scores: { smc: 22, wyckoff: 14, microstructure: -3, momentum: 9 },
    sparkline: [100, 101, 99, 103, 105, 104, 107, 108, 110, 109, 111, 112, 113, 110, 115, 116, 117, 118, 120, 119],
    ...overrides,
  };
}

describe("SignalCard", () => {
  test("renders symbol, full_name, and ±points badge in row 1", () => {
    render(<SignalCard card={makeCard()} />);
    expect(screen.getByText("BTC/USDT")).toBeInTheDocument();
    expect(screen.getByText("Bitcoin")).toBeInTheDocument();
    expect(screen.getByText("+12")).toBeInTheDocument();
  });

  test("renders LONG pill and 4h htf direction in row 2", () => {
    render(<SignalCard card={makeCard()} />);
    expect(screen.getByText("LONG")).toBeInTheDocument();
    expect(screen.getByText(/4h LONG/)).toBeInTheDocument();
  });

  test("renders Confirmed badge for STANDARD/A+ tiers", () => {
    const { rerender } = render(<SignalCard card={makeCard({ signal_tier: "STANDARD" })} />);
    expect(screen.getByText(/CONFIRMED/i)).toBeInTheDocument();
    rerender(<SignalCard card={makeCard({ signal_tier: "A+" })} />);
    expect(screen.getByText(/CONFIRMED/i)).toBeInTheDocument();
  });

  test("renders Probable badge for PAPER/SMALL tiers", () => {
    const { rerender } = render(<SignalCard card={makeCard({ signal_tier: "PAPER" })} />);
    expect(screen.getByText(/PROBABLE/i)).toBeInTheDocument();
    rerender(<SignalCard card={makeCard({ signal_tier: "SMALL" })} />);
    expect(screen.getByText(/PROBABLE/i)).toBeInTheDocument();
  });

  test("renders confidence percentage and bar in row 4", () => {
    const { container } = render(<SignalCard card={makeCard({ confidence: 73 })} />);
    expect(screen.getByText(/Conf 73%/)).toBeInTheDocument();
    // bar fill width style should be 73%
    const bars = container.querySelectorAll("div[style]");
    const fill = Array.from(bars).find((el) => (el as HTMLElement).style.width === "73%");
    expect(fill).toBeDefined();
  });

  test("renders all 4 score chips in row 5", () => {
    render(<SignalCard card={makeCard()} />);
    expect(screen.getByText(/smc \+22/i)).toBeInTheDocument();
    expect(screen.getByText(/wyckoff \+14/i)).toBeInTheDocument();
    expect(screen.getByText(/microstructure -3/i)).toBeInTheDocument();
    expect(screen.getByText(/momentum \+9/i)).toBeInTheDocument();
  });

  test("renders sparkline SVG path", () => {
    const { container } = render(<SignalCard card={makeCard()} />);
    const path = container.querySelector("svg path");
    expect(path).not.toBeNull();
    expect(path?.getAttribute("d")).toMatch(/^M /);
  });

  test("formats ±%change with sign in row 3", () => {
    const { rerender } = render(<SignalCard card={makeCard({ pct_change: 3.42 })} />);
    expect(screen.getByText("+3.42%")).toBeInTheDocument();
    rerender(<SignalCard card={makeCard({ pct_change: -1.7 })} />);
    expect(screen.getByText("-1.70%")).toBeInTheDocument();
  });

  test("clicking the card invokes onClick callback with the card", () => {
    const onClick = vi.fn();
    render(<SignalCard card={makeCard()} onClick={onClick} />);
    fireEvent.click(screen.getByTestId("signal-card-BTC/USDT"));
    expect(onClick).toHaveBeenCalledTimes(1);
    expect(onClick.mock.calls[0]?.[0]).toMatchObject({ symbol: "BTC/USDT" });
  });
});
