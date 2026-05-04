import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { api, type LivePrediction, type SignalMarkers } from "@/lib/api";

const BASE = "/api/v1";

function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    json: async () => body,
  } as unknown as Response;
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function lastUrl(): string {
  const call = fetchMock.mock.calls[0];
  if (!call) throw new Error("fetch was not called");
  return String(call[0]);
}

const SAMPLE_PRED: LivePrediction = {
  symbol: "BTC/USDT",
  timeframe: "1h",
  ts: "2026-05-01T00:00:00Z",
  price: 100,
  final: { score: 0.5, direction: "LONG", confidence: 0.7, contributing_layers: [] },
  layer_scores: {},
  trade_setup: { direction: "LONG", entry: 100, stop_loss: 95, take_profit: 110, risk_reward: 2 },
  momentum: { rsi: 50, macd_line: 0, macd_signal: 0, macd_hist: 0 },
  cold_start: false,
  inputs_hash: "abc",
};

describe("api.predict", () => {
  test("no signal id → bare URL", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(SAMPLE_PRED));
    await api.predict("BTC-USDT", "1h");
    expect(lastUrl()).toBe(`${BASE}/predict/BTC-USDT/1h`);
  });

  test("with signal id → appends ?signal=<id>", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(SAMPLE_PRED));
    await api.predict("BTC-USDT", "1h", "abc123");
    expect(lastUrl()).toBe(`${BASE}/predict/BTC-USDT/1h?signal=abc123`);
  });

  test("URL-encodes signal id with special characters", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(SAMPLE_PRED));
    await api.predict("BTC-USDT", "1h", "a b/c");
    expect(lastUrl()).toBe(`${BASE}/predict/BTC-USDT/1h?signal=a%20b%2Fc`);
  });

  test("undefined signal id behaves as no signal id", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(SAMPLE_PRED));
    await api.predict("BTC-USDT", "1h", undefined);
    expect(lastUrl()).toBe(`${BASE}/predict/BTC-USDT/1h`);
  });

  test("returns parsed LivePrediction with signal_markers when present", async () => {
    const markers: SignalMarkers = {
      signal_id: "sig-1",
      direction: "LONG",
      entry_price: 100,
      stop_loss: 95,
      take_profit: 110,
      opened_at: "2026-05-01T00:00:00Z",
      closed_at: null,
      exit_price: null,
      exit_reason: null,
    };
    const withMarkers: LivePrediction = { ...SAMPLE_PRED, signal_markers: markers };
    fetchMock.mockResolvedValueOnce(jsonResponse(withMarkers));
    const result = await api.predict("BTC-USDT", "1h", "sig-1");
    expect(result.signal_markers).toEqual(markers);
  });
});
