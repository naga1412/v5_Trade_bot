import { act, render, screen, waitFor } from "@testing-library/react";
import App from "@/App";
import { __resetCurrentUserForTests } from "@/hooks/useCurrentUser";
import type { MeOut } from "@/lib/api";

// jsdom doesn't support TradingView's iframe; useLivePrediction also opens a
// WebSocket. Stub both so App can mount.
vi.mock("@/components/chart/TVChart", () => ({
  TVChart: () => <div data-testid="tv-chart-stub" />,
}));
vi.mock("@/hooks/useLivePrediction", () => ({
  useLivePrediction: () => ({ data: null }),
}));
// BotStatus opens REST + a WS. Stub both so App can mount the second tab.
// Also stub all /me + /admin endpoints since useCurrentUser fetches /me.
vi.mock("@/lib/api", () => ({
  api: {
    botOverview: vi.fn().mockReturnValue(new Promise(() => { /* pending */ })),
    promotionGate: vi.fn().mockReturnValue(new Promise(() => { /* pending */ })),
    openPositions: vi.fn().mockReturnValue(new Promise(() => { /* pending */ })),
    perAssetStats: vi.fn().mockReturnValue(new Promise(() => { /* pending */ })),
    longShort: vi.fn().mockReturnValue(new Promise(() => { /* pending */ })),
    equityCurve: vi.fn().mockReturnValue(new Promise(() => { /* pending */ })),
    recentTrades: vi.fn().mockReturnValue(new Promise(() => { /* pending */ })),
    me: vi.fn(),
    mePatch: vi.fn(),
    meBinanceKeys: vi.fn(),
    meTelegram: vi.fn(),
    meTotpSetup: vi.fn(),
    meTotpVerify: vi.fn(),
    adminListUsers: vi.fn().mockResolvedValue([]),
    adminCreateInvitation: vi.fn(),
    adminPatchUser: vi.fn(),
    adminDeleteUser: vi.fn(),
    adminImpersonate: vi.fn(),
    adminImpersonateClear: vi.fn().mockResolvedValue(undefined),
    adminAuditTrail: vi.fn().mockResolvedValue([]),
  },
  // SP-PAUSE: PausedBanner mounts globally and self-polls; stub returns
  // running state so the banner stays a no-op in App-level tests.
  getSystemState: vi.fn().mockResolvedValue({
    paused: false, since: null, by_email: null, reason: null,
  }),
}));
vi.mock("@/hooks/useShadowUpdates", () => ({
  useShadowUpdates: () => ({
    lastOpened: null,
    lastClosed: null,
    lastPnlTick: {},
    lastUniverseRefresh: null,
  }),
}));

import { api } from "@/lib/api";
const mockedMe = vi.mocked(api.me);
const mockedClear = vi.mocked(api.adminImpersonateClear);

function meSample(overrides: Partial<MeOut> = {}): MeOut {
  return {
    id: 1,
    email: "user@example.com",
    display_name: "Test User",
    is_admin: false,
    is_impersonating: false,
    trading_mode: "manual",
    position_sizing_mode: "fixed",
    fixed_size_min_usdt: 20,
    fixed_size_max_usdt: 50,
    max_concurrent_positions: 5,
    max_leverage_cap: 3,
    quiet_hours_enabled: false,
    quiet_hours_start: null,
    quiet_hours_end: null,
    binance_keys_configured: false,
    telegram_configured: false,
    totp_configured: false,
    ...overrides,
  };
}

beforeEach(() => {
  __resetCurrentUserForTests();
  mockedMe.mockReset();
  mockedClear.mockReset();
  mockedClear.mockResolvedValue(undefined);
  window.location.hash = "";
});

afterEach(() => {
  __resetCurrentUserForTests();
});

test("renders TabNav with the default 3 tab labels (Settings always visible)", async () => {
  mockedMe.mockResolvedValueOnce(meSample());
  render(<App />);
  await waitFor(() => {
    expect(screen.getByRole("tab", { name: /settings/i })).toBeInTheDocument();
  });
  expect(screen.getByRole("tab", { name: /live prediction/i })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: /bot status/i })).toBeInTheDocument();
  expect(screen.queryByRole("tab", { name: /^admin$/i })).toBeNull();
});

test("Admin tab visible when current user is_admin=true", async () => {
  mockedMe.mockResolvedValueOnce(meSample({ is_admin: true }));
  render(<App />);
  await waitFor(() => {
    expect(screen.getByRole("tab", { name: /^admin$/i })).toBeInTheDocument();
  });
});

test("Live Prediction tab content renders by default", async () => {
  mockedMe.mockResolvedValueOnce(meSample());
  render(<App />);
  await waitFor(() => {
    expect(screen.getByRole("tab", { name: /settings/i })).toBeInTheDocument();
  });
  // Tab1 mounts its own TopNav (the symbol input is unique to it).
  expect(screen.getByLabelText(/symbol/i)).toBeInTheDocument();
  expect(screen.queryByText(/under construction/i)).not.toBeInTheDocument();
});

test("hash #/bot-status renders BotStatus tab", async () => {
  mockedMe.mockResolvedValueOnce(meSample());
  render(<App />);
  await waitFor(() => {
    expect(screen.getByRole("tab", { name: /settings/i })).toBeInTheDocument();
  });
  act(() => {
    window.location.hash = "#/bot-status";
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  });
  // Each section panel renders its own title; "Promotion Gate" is unique to BotStatus.
  expect(screen.getByText(/promotion gate/i)).toBeInTheDocument();
});

test("ImpersonationBanner is hidden when not impersonating", async () => {
  mockedMe.mockResolvedValueOnce(meSample({ is_admin: true }));
  render(<App />);
  await waitFor(() => {
    expect(screen.getByRole("tab", { name: /^admin$/i })).toBeInTheDocument();
  });
  expect(screen.queryByTestId("impersonation-banner")).toBeNull();
});

test("ImpersonationBanner is shown when is_impersonating=true", async () => {
  mockedMe.mockResolvedValueOnce(
    meSample({ email: "target@example.com", is_admin: true, is_impersonating: true }),
  );
  render(<App />);
  await waitFor(() => {
    expect(screen.getByTestId("impersonation-banner")).toBeInTheDocument();
  });
  expect(screen.getByText(/target@example.com/)).toBeInTheDocument();
});

test("clicking Exit Impersonation calls api.adminImpersonateClear", async () => {
  mockedMe.mockResolvedValueOnce(
    meSample({ email: "target@example.com", is_admin: true, is_impersonating: true }),
  );
  // Stub window.location.reload via defineProperty to avoid jsdom error.
  const reloadSpy = vi.fn();
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { ...window.location, hash: "", reload: reloadSpy },
  });

  render(<App />);
  await waitFor(() => {
    expect(screen.getByTestId("impersonation-banner")).toBeInTheDocument();
  });
  // After exit click, the second /me load (from reload()) should return non-impersonated.
  mockedMe.mockResolvedValueOnce(meSample({ is_admin: true }));
  const btn = screen.getByRole("button", { name: /exit impersonation/i });
  await act(async () => {
    btn.click();
    // Allow the click handler's microtasks to settle.
    await Promise.resolve();
    await Promise.resolve();
  });
  expect(mockedClear).toHaveBeenCalledTimes(1);
});
