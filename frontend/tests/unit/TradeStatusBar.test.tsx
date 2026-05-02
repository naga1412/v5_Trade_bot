import { render, screen } from "@testing-library/react";
import { TradeStatusBar } from "@/tabs/Tab1LivePrediction/panels/TradeStatusBar";

const mockLong = {
  symbol: "BTC/USDT", timeframe: "1h", ts: "2026-05-01T12:00:00Z",
  price: 100, final: { score: 0.5, direction: "LONG" as const, confidence: 0.7,
                        contributing_layers: [1] },
  layer_scores: {}, trade_setup: { direction: "LONG" as const, entry: 100,
    stop_loss: 95, take_profit: 110, risk_reward: 2 },
  momentum: { rsi: 60, macd_line: 1, macd_signal: 0.5, macd_hist: 0.5 },
  cold_start: false, inputs_hash: "abc",
};

test("renders LONG with green color", () => {
  render(<TradeStatusBar data={mockLong} />);
  expect(screen.getByText("LONG")).toHaveClass("text-green");
});

test("renders dash when no data", () => {
  render(<TradeStatusBar data={null} />);
  expect(screen.getByText("—")).toBeInTheDocument();
});
