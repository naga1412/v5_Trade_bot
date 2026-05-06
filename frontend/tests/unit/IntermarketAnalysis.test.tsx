import { render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";

import { IntermarketAnalysis } from "@/tabs/Tab1LivePrediction/panels/IntermarketAnalysis";
import * as api from "@/lib/api";
import type { LivePrediction } from "@/lib/api";


const base: LivePrediction = {
  symbol: "BTC/USDT", timeframe: "1h", ts: "2026-05-06T12:00:00Z",
  price: 70000,
  final: { score: 0, direction: "NEUTRAL", confidence: 0, contributing_layers: [] },
  layer_scores: {} as never,
  trade_setup: { direction: "NEUTRAL", entry: null, stop_loss: null,
                 take_profit: null, risk_reward: null },
  momentum: { rsi: null, macd_line: null, macd_signal: null, macd_hist: null },
  cold_start: false, inputs_hash: "x",
};


test("renders DXY + Gold 30d correlations when API returns values", async () => {
  vi.spyOn(api, "getIntermarket").mockResolvedValue({
    symbol: "BTC/USDT", funding_rate: null, mark_price: null,
    open_interest: null, open_interest_delta_24h_pct: null,
    dxy_correlation_30d: -0.42, gold_correlation_30d: 0.18,
    captured_at: "2026-05-06T12:00:00Z",
  });
  render(<IntermarketAnalysis data={base} />);
  await waitFor(() => {
    expect(screen.getByText("-0.42")).toBeInTheDocument();
    expect(screen.getByText("0.18")).toBeInTheDocument();
  });
});


test("strong correlation (|corr| > 0.5) uses red color class", async () => {
  vi.spyOn(api, "getIntermarket").mockResolvedValue({
    symbol: "BTC/USDT", funding_rate: null, mark_price: null,
    open_interest: null, open_interest_delta_24h_pct: null,
    dxy_correlation_30d: -0.65, gold_correlation_30d: 0.10,
    captured_at: "2026-05-06T12:00:00Z",
  });
  const { container } = render(<IntermarketAnalysis data={base} />);
  await waitFor(() => {
    expect(container.querySelector(".text-red-400")).not.toBeNull();
  });
});


test("renders 'no data' when correlations are null", async () => {
  vi.spyOn(api, "getIntermarket").mockResolvedValue({
    symbol: "BTC/USDT", funding_rate: null, mark_price: null,
    open_interest: null, open_interest_delta_24h_pct: null,
    dxy_correlation_30d: null, gold_correlation_30d: null,
    captured_at: "2026-05-06T12:00:00Z",
  });
  render(<IntermarketAnalysis data={base} />);
  await waitFor(() => {
    expect(screen.getAllByText("no data").length).toBe(2);
  });
});
