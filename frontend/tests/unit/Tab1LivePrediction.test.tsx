import { render } from "@testing-library/react";
import { Tab1LivePrediction } from "@/tabs/Tab1LivePrediction";

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
});

test("forwards query.signal to useLivePrediction", () => {
  useHashRouteMock.mockReturnValue({
    tab: "live-prediction",
    query: { signal: "sig-xyz" },
    setTab: vi.fn(),
  });
  render(<Tab1LivePrediction />);
  expect(useLivePredictionMock).toHaveBeenCalledWith("BTC/USDT", "1h", "sig-xyz");
});

test("passes undefined when no signal in query", () => {
  useHashRouteMock.mockReturnValue({
    tab: "live-prediction",
    query: {},
    setTab: vi.fn(),
  });
  render(<Tab1LivePrediction />);
  expect(useLivePredictionMock).toHaveBeenCalledWith("BTC/USDT", "1h", undefined);
});
