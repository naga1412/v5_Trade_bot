import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";

import { OiFundingRate } from "@/tabs/Tab1LivePrediction/panels/OiFundingRate";
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


function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>;
}


test("renders funding rate, OI and 24h delta when API returns snapshot", async () => {
  vi.spyOn(api, "getIntermarket").mockResolvedValue({
    symbol: "BTC/USDT", funding_rate: -0.0012, mark_price: 70000.0,
    open_interest: 1.30e9, open_interest_delta_24h_pct: 0.30,
    dxy_correlation_30d: null, gold_correlation_30d: null,
    captured_at: "2026-05-06T12:00:00Z",
  });
  render(wrap(<OiFundingRate data={base} />));
  await waitFor(() => {
    expect(screen.getByText(/-0\.12%/)).toBeInTheDocument();   // funding 4-decimals as percent
    expect(screen.getByText(/30\.0%/)).toBeInTheDocument();    // delta
  });
});


test("renders 'no data' placeholder while loading", async () => {
  vi.spyOn(api, "getIntermarket").mockImplementation(() => new Promise(() => {})); // never resolves
  render(wrap(<OiFundingRate data={base} />));
  expect(screen.getAllByText("no data").length).toBeGreaterThanOrEqual(2);
});


test("renders 'no data' placeholder on API error", async () => {
  vi.spyOn(api, "getIntermarket").mockRejectedValue(new Error("404"));
  render(wrap(<OiFundingRate data={base} />));
  await waitFor(() => {
    expect(screen.getAllByText("no data").length).toBeGreaterThanOrEqual(2);
  });
});


test("negative funding rate uses red color class", async () => {
  vi.spyOn(api, "getIntermarket").mockResolvedValue({
    symbol: "BTC/USDT", funding_rate: -0.005, mark_price: null,
    open_interest: 1e9, open_interest_delta_24h_pct: 0.0,
    dxy_correlation_30d: null, gold_correlation_30d: null,
    captured_at: "2026-05-06T12:00:00Z",
  });
  const { container } = render(wrap(<OiFundingRate data={base} />));
  await waitFor(() => {
    expect(container.querySelector(".text-red-400")).not.toBeNull();
  });
});
