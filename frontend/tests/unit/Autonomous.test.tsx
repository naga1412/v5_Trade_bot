import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { Autonomous } from "@/tabs/Autonomous";
import * as currentUserHook from "@/hooks/useCurrentUser";

const baseUser = {
  id: 1,
  email: "u@x.com",
  display_name: "u",
  is_admin: false,
  is_impersonating: false,
  trading_mode: "manual" as const,
  position_sizing_mode: "fixed" as const,
  fixed_size_min_usdt: 30,
  fixed_size_max_usdt: 30,
  max_concurrent_positions: 5,
  max_leverage_cap: 10,
  quiet_hours_enabled: false,
  quiet_hours_start: null,
  quiet_hours_end: null,
  binance_keys_configured: false,
  telegram_configured: false,
  totp_configured: false,
};

beforeEach(() => {
  // Pin /me so ModeSwitcher renders the active "Manual" button without
  // hitting the network (no MSW in this suite).
  vi.spyOn(currentUserHook, "useCurrentUser").mockReturnValue({
    user: baseUser,
    isAdmin: false,
    isImpersonating: false,
    isLoading: false,
    error: null,
    reload: vi.fn().mockResolvedValue(undefined),
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("Autonomous tab", () => {
  test("renders all 7 panels", () => {
    render(<Autonomous />);
    // Each panel has a unique <h3> title. Use heading role rather than
    // free text so the assertion doesn't collide with body copy that
    // happens to mention the same words (e.g. "Live positions will
    // appear here..." in the empty-state paragraph).
    for (const title of [
      /Trading Mode/i,
      /Promotion Gates/i,
      /Live Positions/i,
      /Kill Switches/i,
      /Recent Activity/i,
      /Tax Export/i,
      /^Settings$/i,
    ]) {
      expect(screen.getByRole("heading", { name: title })).toBeInTheDocument();
    }
  });

  test("ModeSwitcher renders three clickable mode buttons", () => {
    render(<Autonomous />);
    // Phase J: locks removed; clicking an upgrade button now opens a
    // TOTP confirm form instead. See ModeSwitcher.test.tsx for the
    // interactive coverage.
    for (const label of [/^manual$/i, /telegram approve/i, /fully auto/i]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
    expect(
      screen.getByRole("button", { name: /^manual$/i }),
    ).toHaveAttribute("aria-pressed", "true");
  });

  test("KillSwitches lists all 6 switches from spec §11.1", () => {
    render(<Autonomous />);
    for (const switchName of [
      "Daily loss",
      "Consecutive losses",
      "Network outage",
      "Slippage",
      "Liquidation near",
      "Funding rate guard",
    ]) {
      expect(screen.getByText(switchName)).toBeInTheDocument();
    }
  });

  test("GateStatus shows all 6 gate metrics from spec §4.1/4.2", () => {
    render(<Autonomous />);
    // Note: "Closed trades" also appears in TaxExport, so assert it via
    // its row-context (use getAllByText and assert the count instead of
    // getByText which would trip the multi-match guard).
    for (const metric of [
      "Days continuous",
      "Sharpe (annualised)",
      "Max drawdown",
      "Win rate",
      "Profit factor",
    ]) {
      expect(screen.getByText(metric)).toBeInTheDocument();
    }
    // "Closed trades" intentionally appears in both GateStatus and
    // TaxExport (same metric, different aggregation window).
    expect(screen.getAllByText("Closed trades")).toHaveLength(2);
  });

  test("TaxExport shows the financial year", () => {
    render(<Autonomous />);
    expect(screen.getByText("FY2026-27")).toBeInTheDocument();
  });
});
