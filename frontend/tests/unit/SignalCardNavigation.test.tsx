// SP-6 Phase D8: integration test for SignalCard click → Tab 1 deeplink.
//
// BullishColumn + BearishColumn map each card's onClick to a hash-route
// navigation: `#/live-prediction?symbol=<encoded>&tf=<encoded>`. Tab 1's
// query parser (Phase C5) reads `?symbol/?tf` and seeds its own state.
//
// We assert here that the hash actually changes when the underlying card
// button is clicked. The string format is load-bearing — the meta-plan calls
// for `encodeURIComponent` so symbols like `BTC/USDT` round-trip safely.

import { render, fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test } from "vitest";
import type { SignalCard as SignalCardData } from "@/lib/api";
import { BullishColumn } from "@/tabs/Tab3Scanner/BullishColumn";
import { BearishColumn } from "@/tabs/Tab3Scanner/BearishColumn";
import { buildLivePredictionHash } from "@/tabs/Tab3Scanner/applyFilter";

function makeCard(symbol: string): SignalCardData {
  return {
    symbol,
    full_name: "Asset",
    is_favorite: false,
    points: 1,
    pct_change: 0.5,
    direction: "LONG",
    htf_direction: "LONG",
    signal_tier: "STANDARD",
    hybrid_flag: null,
    ai_score: 50,
    wyckoff_phase: "Markup",
    confidence: 60,
    scores: { smc: 1, wyckoff: 1, microstructure: 1, momentum: 1 },
    sparkline: [1, 2, 3],
  };
}

beforeEach(() => {
  window.location.hash = "";
});

afterEach(() => {
  window.location.hash = "";
});

describe("SignalCard navigation", () => {
  test("buildLivePredictionHash encodes symbol + tf", () => {
    expect(buildLivePredictionHash("BTC/USDT", "1h")).toBe(
      "#/live-prediction?symbol=BTC%2FUSDT&tf=1h",
    );
    expect(buildLivePredictionHash("ETH/USDT", "4h")).toBe(
      "#/live-prediction?symbol=ETH%2FUSDT&tf=4h",
    );
  });

  test("clicking a bullish card sets the live-prediction hash with encoded symbol", () => {
    render(
      <BullishColumn
        cards={[makeCard("BTC/USDT")]}
        search=""
        filter="all"
        tf="1h"
      />,
    );
    fireEvent.click(screen.getByTestId("signal-card-BTC/USDT"));
    expect(window.location.hash).toBe(
      "#/live-prediction?symbol=BTC%2FUSDT&tf=1h",
    );
  });

  test("clicking a bearish card sets the live-prediction hash with the active tf", () => {
    render(
      <BearishColumn
        cards={[makeCard("DOGE/USDT")]}
        search=""
        filter="all"
        tf="4h"
      />,
    );
    fireEvent.click(screen.getByTestId("signal-card-DOGE/USDT"));
    expect(window.location.hash).toBe(
      "#/live-prediction?symbol=DOGE%2FUSDT&tf=4h",
    );
  });

  test("symbols containing characters needing percent-encoding survive the round-trip", () => {
    render(
      <BullishColumn
        cards={[makeCard("BTC USDT&perp")]}
        search=""
        filter="all"
        tf="15m"
      />,
    );
    fireEvent.click(screen.getByTestId("signal-card-BTC USDT&perp"));
    expect(window.location.hash).toBe(
      "#/live-prediction?symbol=" +
        encodeURIComponent("BTC USDT&perp") +
        "&tf=15m",
    );
  });
});
