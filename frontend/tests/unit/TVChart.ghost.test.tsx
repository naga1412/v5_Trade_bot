/**
 * SP-1 Phase D4: TVChart ghost candle rendering.
 *
 * The chart adds a SECOND candlestick series for the ghost candle and renders
 * it one timeframe ahead of `liveTs` with a translucent color. Two price
 * lines (P5 and P95) bracket the uncertainty band.
 *
 * Mock strategy: lightweight-charts createChart returns a chart whose
 * `addCandlestickSeries` returns a fresh series object on each call — first
 * call is the real series (kept by the existing data effect), second is the
 * ghost series. Tests assert call counts + arguments.
 */
import { render } from "@testing-library/react";
import type { GhostCandle } from "@/lib/api";

const mocks = vi.hoisted(() => {
  const realSeries = {
    setData: vi.fn<(d: unknown) => void>(),
    update: vi.fn<(d: unknown) => void>(),
    createPriceLine: vi.fn<(opts: Record<string, unknown>) => unknown>(
      () => ({}),
    ),
    removePriceLine: vi.fn<(line: unknown) => void>(),
    setMarkers: vi.fn<(m: unknown[]) => void>(),
  };
  const ghostSeries = {
    setData: vi.fn<(d: unknown) => void>(),
    update: vi.fn<(d: unknown) => void>(),
    createPriceLine: vi.fn<(opts: Record<string, unknown>) => unknown>(
      () => ({}),
    ),
    removePriceLine: vi.fn<(line: unknown) => void>(),
    setMarkers: vi.fn<(m: unknown[]) => void>(),
  };
  let callCount = 0;
  const chart = {
    addCandlestickSeries: vi.fn(() => {
      callCount += 1;
      return callCount === 1 ? realSeries : ghostSeries;
    }),
    remove: vi.fn(),
    removeSeries: vi.fn(),
    timeScale: vi.fn(() => ({ fitContent: vi.fn() })),
  };
  return {
    realSeries,
    ghostSeries,
    chart,
    createChart: vi.fn(() => chart),
    resetCallCount: () => {
      callCount = 0;
    },
  };
});

vi.mock("lightweight-charts", () => ({
  createChart: mocks.createChart,
  LineStyle: { Solid: 0, Dashed: 1, Dotted: 2, LargeDashed: 3, SparseDotted: 4 },
}));

vi.mock("@/hooks/useChartHistory", () => ({
  useChartHistory: () => [],
}));

import { TVChart } from "@/components/chart/TVChart";

beforeEach(() => {
  mocks.realSeries.setData.mockClear();
  mocks.realSeries.createPriceLine.mockClear();
  mocks.realSeries.removePriceLine.mockClear();
  mocks.realSeries.setMarkers.mockClear();
  mocks.ghostSeries.setData.mockClear();
  mocks.ghostSeries.createPriceLine.mockClear();
  mocks.ghostSeries.removePriceLine.mockClear();
  mocks.chart.addCandlestickSeries.mockClear();
  mocks.chart.remove.mockClear();
  mocks.chart.removeSeries.mockClear();
  mocks.createChart.mockClear();
  mocks.resetCallCount();
});

const SAMPLE_GHOST: GhostCandle = {
  open: 80100,
  high: 80300,
  low: 79900,
  close: 80200,
  p5_low: 79500,
  p95_high: 80800,
  uncertainty: 0.005,
};

interface CandleBar {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
}

interface PriceLineOpts {
  price: number;
  title: string;
}

test("does not add a second series when ghost is null", () => {
  render(
    <TVChart
      symbol="BTC/USDT"
      timeframe="1h"
      liveTs="2026-05-05T12:00:00Z"
      livePrice={80000}
      ghost={null}
    />,
  );
  expect(mocks.chart.addCandlestickSeries).toHaveBeenCalledTimes(1);
  expect(mocks.ghostSeries.setData).not.toHaveBeenCalled();
});

test("does not add a second series when ghost prop is omitted", () => {
  render(<TVChart symbol="BTC/USDT" timeframe="1h" />);
  expect(mocks.chart.addCandlestickSeries).toHaveBeenCalledTimes(1);
});

test("renders ghost candle one timeframe ahead of liveTs", () => {
  render(
    <TVChart
      symbol="BTC/USDT"
      timeframe="1h"
      liveTs="2026-05-05T12:00:00Z"
      livePrice={80000}
      ghost={SAMPLE_GHOST}
    />,
  );
  // Real series + ghost series.
  expect(mocks.chart.addCandlestickSeries).toHaveBeenCalledTimes(2);
  // Ghost series got setData called with a single bar.
  expect(mocks.ghostSeries.setData).toHaveBeenCalledTimes(1);
  const args = mocks.ghostSeries.setData.mock.calls[0]?.[0] as CandleBar[];
  expect(args).toHaveLength(1);
  expect(args[0]?.open).toBe(80100);
  expect(args[0]?.close).toBe(80200);
  // 12:00 UTC + 1h = 13:00 UTC = 2026-05-05T13:00:00Z = 1778411600
  const expectedTs =
    Math.floor(new Date("2026-05-05T12:00:00Z").getTime() / 1000) + 3600;
  expect(args[0]?.time).toBe(expectedTs);
});

test("creates two price lines (P5 + P95) on ghost series", () => {
  render(
    <TVChart
      symbol="BTC/USDT"
      timeframe="1h"
      liveTs="2026-05-05T12:00:00Z"
      livePrice={80000}
      ghost={SAMPLE_GHOST}
    />,
  );
  expect(mocks.ghostSeries.createPriceLine).toHaveBeenCalledTimes(2);
  const calls = mocks.ghostSeries.createPriceLine.mock.calls.map(
    (c) => c[0] as unknown as PriceLineOpts,
  );
  const titles = calls.map((c) => c.title).sort();
  expect(titles).toEqual(["P5", "P95"]);
  expect(calls.find((c) => c.title === "P5")?.price).toBe(79500);
  expect(calls.find((c) => c.title === "P95")?.price).toBe(80800);
});

test("does not add a ghost series when liveTs is missing (cannot anchor)", () => {
  render(
    <TVChart
      symbol="BTC/USDT"
      timeframe="1h"
      livePrice={80000}
      ghost={SAMPLE_GHOST}
    />,
  );
  // No liveTs → cannot compute ghostTs → no ghost series created.
  expect(mocks.chart.addCandlestickSeries).toHaveBeenCalledTimes(1);
  expect(mocks.ghostSeries.setData).not.toHaveBeenCalled();
});

test("removes ghost series when ghost transitions to null", () => {
  const { rerender } = render(
    <TVChart
      symbol="BTC/USDT"
      timeframe="1h"
      liveTs="2026-05-05T12:00:00Z"
      livePrice={80000}
      ghost={SAMPLE_GHOST}
    />,
  );
  expect(mocks.chart.addCandlestickSeries).toHaveBeenCalledTimes(2);
  mocks.chart.removeSeries.mockClear();
  rerender(
    <TVChart
      symbol="BTC/USDT"
      timeframe="1h"
      liveTs="2026-05-05T12:00:00Z"
      livePrice={80000}
      ghost={null}
    />,
  );
  expect(mocks.chart.removeSeries).toHaveBeenCalled();
});
