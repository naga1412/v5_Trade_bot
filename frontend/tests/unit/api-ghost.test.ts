import { describe, it, expect } from "vitest";
import type { LivePrediction, GhostCandle } from "@/lib/api";

describe("GhostCandle / LivePrediction.ghost typing", () => {
  it("LivePrediction has optional ghost field accepting null", () => {
    const p: LivePrediction = {
      symbol: "BTC/USDT",
      timeframe: "1h",
      ts: "2026-05-05T12:00:00Z",
      price: 80000,
      final: {
        score: 0.5,
        direction: "LONG",
        confidence: 0.7,
        contributing_layers: [1],
      },
      layer_scores: {},
      trade_setup: {
        direction: "LONG",
        entry: null,
        stop_loss: null,
        take_profit: null,
        risk_reward: null,
      },
      momentum: { rsi: null, macd_line: null, macd_signal: null, macd_hist: null },
      cold_start: false,
      inputs_hash: "h",
      ghost: null,
    };
    expect(p.ghost).toBeNull();
  });

  it("ghost can be a GhostCandle object", () => {
    const ghost: GhostCandle = {
      open: 80100,
      high: 80300,
      low: 79900,
      close: 80200,
      p5_low: 79500,
      p95_high: 80800,
      uncertainty: 0.005,
    };
    expect(ghost.uncertainty).toBeGreaterThanOrEqual(0);
    expect(ghost.close).toBe(80200);
  });
});
