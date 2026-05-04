import { render } from "@testing-library/react";
import type { SignalMarkers } from "@/lib/api";

// Capture series mock so tests can assert on its calls. Hoisted via vi.hoisted
// because vi.mock factories run before module top-level code.
const mocks = vi.hoisted(() => {
  const series = {
    setData: vi.fn<(d: unknown) => void>(),
    update: vi.fn<(d: unknown) => void>(),
    createPriceLine: vi.fn<(opts: Record<string, unknown>) => { applyOptions: () => void }>(
      () => ({ applyOptions: vi.fn() }),
    ),
    removePriceLine: vi.fn<(line: unknown) => void>(),
    setMarkers: vi.fn<(m: unknown[]) => void>(),
  };
  const chart = {
    addCandlestickSeries: vi.fn(() => series),
    remove: vi.fn(),
    timeScale: vi.fn(() => ({ fitContent: vi.fn() })),
  };
  return {
    series,
    chart,
    createChart: vi.fn(() => chart),
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
  mocks.series.setData.mockClear();
  mocks.series.update.mockClear();
  mocks.series.createPriceLine.mockClear();
  mocks.series.removePriceLine.mockClear();
  mocks.series.setMarkers.mockClear();
  mocks.chart.addCandlestickSeries.mockClear();
  mocks.chart.remove.mockClear();
  mocks.createChart.mockClear();
});

const SAMPLE_OPEN: SignalMarkers = {
  signal_id: "sig-1",
  direction: "LONG",
  entry_price: 100,
  stop_loss: 95,
  take_profit: 110,
  opened_at: "2026-05-01T12:00:00Z",
  closed_at: null,
  exit_price: null,
  exit_reason: null,
};

const SAMPLE_CLOSED_TP: SignalMarkers = {
  ...SAMPLE_OPEN,
  closed_at: "2026-05-01T16:00:00Z",
  exit_price: 110,
  exit_reason: "TAKE_PROFIT",
};

test("renders without throwing when signalMarkers is null", () => {
  render(<TVChart symbol="BTC/USDT" timeframe="1h" signalMarkers={null} />);
  expect(mocks.series.createPriceLine).not.toHaveBeenCalled();
});

test("renders without throwing when signalMarkers is undefined", () => {
  render(<TVChart symbol="BTC/USDT" timeframe="1h" />);
  expect(mocks.series.createPriceLine).not.toHaveBeenCalled();
});

interface PriceLineOptions {
  title: string;
  price: number;
}

interface MarkerLite {
  text?: string;
  position: string;
  shape: string;
}

function lastMarkerCall(): MarkerLite[] {
  const calls = mocks.series.setMarkers.mock.calls;
  if (calls.length === 0) throw new Error("setMarkers was never called");
  const last = calls[calls.length - 1];
  if (!last) throw new Error("setMarkers last call missing");
  return last[0] as MarkerLite[];
}

test("creates exactly 3 price lines (entry/SL/TP) when signalMarkers provided", () => {
  render(<TVChart symbol="BTC/USDT" timeframe="1h" signalMarkers={SAMPLE_OPEN} />);
  expect(mocks.series.createPriceLine).toHaveBeenCalledTimes(3);
  const calls: PriceLineOptions[] = mocks.series.createPriceLine.mock.calls.map(
    (c) => c[0] as unknown as PriceLineOptions,
  );
  const titles = calls.map((c) => c.title);
  expect(titles).toEqual(["Entry", "SL", "TP"]);
  expect(calls.find((c) => c.title === "Entry")?.price).toBe(100);
  expect(calls.find((c) => c.title === "SL")?.price).toBe(95);
  expect(calls.find((c) => c.title === "TP")?.price).toBe(110);
});

test("places one marker for an open signal (no exit)", () => {
  render(<TVChart symbol="BTC/USDT" timeframe="1h" signalMarkers={SAMPLE_OPEN} />);
  // setMarkers is called twice: once with [] (cleanup at start of effect),
  // then once with the new markers.
  const markers = lastMarkerCall();
  expect(markers).toHaveLength(1);
  expect(markers[0]?.text).toBe("Open");
  expect(markers[0]?.position).toBe("belowBar");
  expect(markers[0]?.shape).toBe("arrowUp");
});

test("places two markers (open + exit) for a closed TAKE_PROFIT LONG signal", () => {
  render(<TVChart symbol="BTC/USDT" timeframe="1h" signalMarkers={SAMPLE_CLOSED_TP} />);
  const markers = lastMarkerCall();
  expect(markers).toHaveLength(2);
  expect(markers[1]?.text).toBe("TAKE_PROFIT");
  expect(markers[1]?.position).toBe("aboveBar");
  expect(markers[1]?.shape).toBe("arrowDown");
});

test("removes price lines and clears markers on unmount", () => {
  const { unmount } = render(
    <TVChart symbol="BTC/USDT" timeframe="1h" signalMarkers={SAMPLE_OPEN} />,
  );
  expect(mocks.series.createPriceLine).toHaveBeenCalledTimes(3);
  mocks.series.removePriceLine.mockClear();
  mocks.series.setMarkers.mockClear();
  unmount();
  // Cleanup removes the 3 lines; cleanup also calls setMarkers([]).
  expect(mocks.series.removePriceLine).toHaveBeenCalledTimes(3);
  const cleared = mocks.series.setMarkers.mock.calls.find(
    (c) => Array.isArray(c[0]) && (c[0] as unknown[]).length === 0,
  );
  expect(cleared).toBeDefined();
});
