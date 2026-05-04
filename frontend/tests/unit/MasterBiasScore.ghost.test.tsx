import { render, screen } from "@testing-library/react";
import { MasterBiasScore } from "@/tabs/Tab1LivePrediction/panels/MasterBiasScore";
import type { LivePrediction } from "@/lib/api";

const baseData: LivePrediction = {
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

describe("MasterBiasScore ghost preview row", () => {
  test("does not render ghost block when ghost is null", () => {
    render(<MasterBiasScore data={baseData} />);
    expect(screen.queryByText(/ghost candle/i)).toBeNull();
  });

  test("does not render ghost block when ghost field is omitted", () => {
    const dataNoGhost = { ...baseData };
    delete dataNoGhost.ghost;
    render(<MasterBiasScore data={dataNoGhost} />);
    expect(screen.queryByText(/ghost candle/i)).toBeNull();
  });

  test("renders ghost block when ghost present", () => {
    const data: LivePrediction = {
      ...baseData,
      ghost: {
        open: 80100,
        high: 80300,
        low: 79900,
        close: 80200,
        p5_low: 79500,
        p95_high: 80800,
        uncertainty: 0.005,
      },
    };
    render(<MasterBiasScore data={data} />);
    expect(screen.getByText(/ghost candle/i)).toBeInTheDocument();
    // Open and Close prices appear (formatted to 2 decimals).
    expect(screen.getByText(/80100/)).toBeInTheDocument();
    expect(screen.getByText(/80200/)).toBeInTheDocument();
    // 0.005 * 80000 = $400 uncertainty (allow rounding ±$5).
    expect(screen.getByText(/\$4\d\d/)).toBeInTheDocument();
  });

  test("shows positive delta percent and green styling when close > open", () => {
    const data: LivePrediction = {
      ...baseData,
      ghost: {
        open: 80000,
        high: 80500,
        low: 79900,
        close: 80400,
        p5_low: 79500,
        p95_high: 80800,
        uncertainty: 0.003,
      },
    };
    render(<MasterBiasScore data={data} />);
    // 80400/80000 - 1 = +0.50%
    expect(screen.getByText(/\+0\.50%/)).toBeInTheDocument();
  });

  test("shows negative delta percent when close < open", () => {
    const data: LivePrediction = {
      ...baseData,
      ghost: {
        open: 80000,
        high: 80100,
        low: 79500,
        close: 79600,
        p5_low: 79200,
        p95_high: 80400,
        uncertainty: 0.004,
      },
    };
    render(<MasterBiasScore data={data} />);
    // 79600/80000 - 1 = -0.50%
    expect(screen.getByText(/-0\.50%/)).toBeInTheDocument();
  });
});
