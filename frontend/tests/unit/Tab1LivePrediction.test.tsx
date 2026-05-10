import { render, screen } from "@testing-library/react";
import { Tab1LivePrediction } from "@/tabs/Tab1LivePrediction";
import type { LayerScore, LivePrediction } from "@/lib/api";

vi.mock("@/components/chart/TVChart", () => ({
  TVChart: () => <div data-testid="tv-chart-stub" />,
}));

const useLivePredictionMock = vi.fn();
vi.mock("@/hooks/useLivePrediction", () => ({
  useLivePrediction: (...args: [string, string, string?]) => useLivePredictionMock(...args),
}));

const useHashRouteMock = vi.fn();
vi.mock("@/lib/useHashRoute", () => ({
  useHashRoute: () => useHashRouteMock(),
}));

beforeEach(() => {
  useLivePredictionMock.mockReset();
  useLivePredictionMock.mockReturnValue({ data: null });
  useHashRouteMock.mockReset();
  useHashRouteMock.mockReturnValue({
    tab: "live-prediction",
    query: {},
    setTab: vi.fn(),
  });
});

test("forwards query.signal to useLivePrediction", () => {
  useHashRouteMock.mockReturnValue({
    tab: "live-prediction",
    query: { signal: "sig-xyz" },
    setTab: vi.fn(),
  });
  render(
  <Tab1LivePrediction
    symbol="BTC/USDT"
    timeframe="1h"
    onTimeframeChange={vi.fn()}
    drawerOpen={false}
    onDrawerClose={vi.fn()}
  />,
);
  expect(useLivePredictionMock).toHaveBeenCalledWith("BTC/USDT", "1h", "sig-xyz");
});

test("passes undefined when no signal in query", () => {
  useHashRouteMock.mockReturnValue({
    tab: "live-prediction",
    query: {},
    setTab: vi.fn(),
  });
  render(
  <Tab1LivePrediction
    symbol="BTC/USDT"
    timeframe="1h"
    onTimeframeChange={vi.fn()}
    drawerOpen={false}
    onDrawerClose={vi.fn()}
  />,
);
  expect(useLivePredictionMock).toHaveBeenCalledWith("BTC/USDT", "1h", undefined);
});

// --- SP-6 Phase C5: all 17 sidebar panels assembled ---

const samplePrediction: LivePrediction = {
  symbol: "BTC/USDT",
  timeframe: "1h",
  ts: "2026-05-01T12:00:00Z",
  price: 100,
  final: { score: 0.4, direction: "LONG", confidence: 0.7, contributing_layers: [1, 2] },
  layer_scores: {
    "1": { direction: "LONG", strength: 0.9, confidence: 0.85, notes: "EMAs aligned ascending" },
    "4": { direction: "LONG", strength: 0.6, confidence: 0.7, notes: "OB sweep above PDH" },
    "8": { direction: "SHORT", strength: 0.6, confidence: 0.8, notes: "supervisor short" },
  } as Record<string, LayerScore | null>,
  trade_setup: { direction: "LONG", entry: 100, stop_loss: 98, take_profit: 105, risk_reward: 2.5 },
  momentum: { rsi: 55, macd_line: 0.1, macd_signal: 0.05, macd_hist: 0.05 },
  cold_start: false,
  inputs_hash: "x",
  ghost: null,
};

test("renders all permanent panel titles (16 always-on panels) when data present", () => {
  useLivePredictionMock.mockReturnValue({ data: samplePrediction });
  render(
  <Tab1LivePrediction
    symbol="BTC/USDT"
    timeframe="1h"
    onTimeframeChange={vi.fn()}
    drawerOpen={false}
    onDrawerClose={vi.fn()}
  />,
);
  // Titles unique to each panel — verify the sidebar wires them all up.
  const titles = [
    /Final Value/i,
    /Long \/ Short Ratio/i,
    /HTF Bias/i,
    /Volume Profile/i,
    /Momentum/i,
    /Microstructure/i,
    /Liquidity Sweep/i,
    /OI & Funding/i,
    /Intermarket/i,
    /Sentiment/i,
    /Ghost Candle/i,
    /Trade Setup/i,
    /Key Levels/i,
    /News & Macro/i,
  ];
  for (const t of titles) {
    expect(screen.getAllByText(t).length).toBeGreaterThan(0);
  }
});

test("renders DeepLearningSupervisor only when L8 layer is present", () => {
  useLivePredictionMock.mockReturnValue({ data: samplePrediction });
  render(
  <Tab1LivePrediction
    symbol="BTC/USDT"
    timeframe="1h"
    onTimeframeChange={vi.fn()}
    drawerOpen={false}
    onDrawerClose={vi.fn()}
  />,
);
  expect(screen.getByText(/Deep Learning Supervisor/i)).toBeInTheDocument();
});

test("hides DeepLearningSupervisor when L8 layer is absent", () => {
  const noL8 = { ...samplePrediction, layer_scores: { "1": samplePrediction.layer_scores["1"] } };
  useLivePredictionMock.mockReturnValue({ data: noL8 });
  render(
  <Tab1LivePrediction
    symbol="BTC/USDT"
    timeframe="1h"
    onTimeframeChange={vi.fn()}
    drawerOpen={false}
    onDrawerClose={vi.fn()}
  />,
);
  expect(screen.queryByText(/Deep Learning Supervisor/i)).not.toBeInTheDocument();
});
