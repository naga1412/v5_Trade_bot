/**
 * Regression: WS clientId must use URL-safe symbol (BTC-USDT), not the raw
 * symbol (BTC/USDT). Raw slash made the WS URL /ws/v1/tab1-BTC/USDT-1h
 * which fails to match the backend route /ws/v1/{client_id} (single segment),
 * silently breaking all live updates on the dashboard.
 */
import { renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const ctorCalls: string[] = [];

vi.mock("@/lib/ws", () => ({
  TradingRadarSocket: class FakeSock {
    constructor(clientId: string) {
      ctorCalls.push(clientId);
    }
    connect() {}
    subscribe() {}
    on() { return () => {}; }
    close() {}
  },
}));

vi.mock("@/lib/api", () => ({
  api: { predict: vi.fn().mockResolvedValue(null) },
}));

import { useLivePrediction } from "@/hooks/useLivePrediction";

beforeEach(() => {
  ctorCalls.length = 0;
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("useLivePrediction", () => {
  test("WS clientId uses URL-safe symbol (no raw slash)", () => {
    renderHook(() => useLivePrediction("BTC/USDT", "1h"));
    expect(ctorCalls).toEqual(["tab1-BTC-USDT-1h"]);
    expect(ctorCalls[0]).not.toContain("/");
  });

  test("WS clientId encodes other slash-bearing pairs the same way", () => {
    renderHook(() => useLivePrediction("ETH/USDT", "5m"));
    expect(ctorCalls).toEqual(["tab1-ETH-USDT-5m"]);
  });
});
