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

test("hash #/bot-status renders BotStatus placeholder", () => {
  render(<App />);
  act(() => {
    window.location.hash = "#/bot-status";
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  });
  expect(screen.getByText(/bot status \(under construction\)/i)).toBeInTheDocument();
  // Tab1's symbol input should no longer be in the tree.
  expect(screen.queryByLabelText(/symbol/i)).not.toBeInTheDocument();
});
