import { act, render, screen } from "@testing-library/react";
import App from "@/App";

// jsdom doesn't support TradingView's iframe; useLivePrediction also opens a
// WebSocket. Stub both so App can mount.
vi.mock("@/components/chart/TVChart", () => ({
  TVChart: () => <div data-testid="tv-chart-stub" />,
}));
vi.mock("@/hooks/useLivePrediction", () => ({
  useLivePrediction: () => ({ data: null }),
}));
// BotStatus opens REST + a WS. Stub both so App can mount the second tab.
vi.mock("@/lib/api", () => ({
  api: {
    botOverview: vi.fn().mockReturnValue(new Promise(() => { /* pending */ })),
    promotionGate: vi.fn().mockReturnValue(new Promise(() => { /* pending */ })),
    openPositions: vi.fn().mockReturnValue(new Promise(() => { /* pending */ })),
    perAssetStats: vi.fn().mockReturnValue(new Promise(() => { /* pending */ })),
    longShort: vi.fn().mockReturnValue(new Promise(() => { /* pending */ })),
    equityCurve: vi.fn().mockReturnValue(new Promise(() => { /* pending */ })),
    recentTrades: vi.fn().mockReturnValue(new Promise(() => { /* pending */ })),
  },
}));
vi.mock("@/hooks/useShadowUpdates", () => ({
  useShadowUpdates: () => ({
    lastOpened: null,
    lastClosed: null,
    lastPnlTick: {},
    lastUniverseRefresh: null,
  }),
}));

beforeEach(() => {
  window.location.hash = "";
});

test("renders TabNav with both tab labels", () => {
  render(<App />);
  expect(screen.getByRole("tab", { name: /live prediction/i })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: /bot status/i })).toBeInTheDocument();
});

test("Live Prediction tab content renders by default", () => {
  render(<App />);
  // Tab1 mounts its own TopNav (the symbol input is unique to it).
  expect(screen.getByLabelText(/symbol/i)).toBeInTheDocument();
  expect(screen.queryByText(/under construction/i)).not.toBeInTheDocument();
});

test("hash #/bot-status renders BotStatus tab", () => {
  render(<App />);
  act(() => {
    window.location.hash = "#/bot-status";
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  });
  // Each section panel renders its own title; "Promotion Gate" is unique to BotStatus.
  expect(screen.getByText(/promotion gate/i)).toBeInTheDocument();
});
