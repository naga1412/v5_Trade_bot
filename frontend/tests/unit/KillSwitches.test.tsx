/**
 * SP-8 Phase J — KillSwitches interactive tests.
 *
 * Covers:
 *  - Loads switches from /me/kill-switches and renders all 6 with state
 *  - Clicking "Disable" without TOTP configured shows error, no API call
 *  - Clicking "Disable" opens TOTP form; confirm fires PATCH with code
 *  - Clicking "Enable" on a disabled switch fires PATCH immediately
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { KillSwitches } from "@/tabs/Autonomous/panels/KillSwitches";
import * as currentUserHook from "@/hooks/useCurrentUser";
import * as apiMod from "@/lib/api";

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

function mockUser(overrides: Partial<typeof baseUser> = {}) {
  vi.spyOn(currentUserHook, "useCurrentUser").mockReturnValue({
    user: { ...baseUser, ...overrides },
    isAdmin: false,
    isImpersonating: false,
    isLoading: false,
    error: null,
    reload: vi.fn().mockResolvedValue(undefined),
  });
}

function defaultSwitchList(over: Partial<apiMod.KillSwitch> = {}) {
  const names: apiMod.KillSwitchName[] = [
    "daily_loss",
    "consecutive_losses",
    "network_outage",
    "slippage",
    "liquidation_near",
    "funding_rate_guard",
  ];
  return {
    switches: names.map((n) => ({
      name: n,
      enabled: true,
      threshold_value: null,
      is_tripped: false,
      tripped_at: null,
      tripped_reason: null,
      default_threshold: 0.02,
      ...over,
    })),
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("KillSwitches", () => {
  test("renders all 6 switches loaded from API", async () => {
    mockUser({ totp_configured: true });
    vi.spyOn(apiMod.api, "meKillSwitches").mockResolvedValue(
      defaultSwitchList(),
    );
    render(<KillSwitches />);
    for (const label of [
      "Daily loss", "Consecutive losses", "Network outage",
      "Slippage", "Liquidation near", "Funding rate guard",
    ]) {
      expect(await screen.findByText(label)).toBeInTheDocument();
    }
    // 6 disable buttons + zero enable buttons (all default-armed).
    expect(
      screen.getAllByRole("button", { name: /disable /i }),
    ).toHaveLength(6);
  });

  test("clicking Disable without TOTP shows error, no API call", async () => {
    mockUser({ totp_configured: false });
    vi.spyOn(apiMod.api, "meKillSwitches").mockResolvedValue(
      defaultSwitchList(),
    );
    const patch = vi.spyOn(apiMod.api, "meKillSwitchPatch");

    render(<KillSwitches />);
    await screen.findByText("Daily loss");
    fireEvent.click(
      screen.getByRole("button", { name: /disable daily loss/i }),
    );
    expect(screen.getByRole("alert")).toHaveTextContent(/configure TOTP/i);
    expect(patch).not.toHaveBeenCalled();
  });

  test("Disable opens TOTP form, confirm PATCHes with code", async () => {
    mockUser({ totp_configured: true });
    vi.spyOn(apiMod.api, "meKillSwitches").mockResolvedValue(
      defaultSwitchList(),
    );
    const patch = vi
      .spyOn(apiMod.api, "meKillSwitchPatch")
      .mockResolvedValue({
        name: "daily_loss", enabled: false, threshold_value: null,
        is_tripped: false, tripped_at: null, tripped_reason: null,
        default_threshold: 0.02,
      });

    render(<KillSwitches />);
    await screen.findByText("Daily loss");
    fireEvent.click(
      screen.getByRole("button", { name: /disable daily loss/i }),
    );
    const input = screen.getByLabelText(/totp code/i) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "654321" } });
    fireEvent.click(screen.getByRole("button", { name: /confirm/i }));

    await waitFor(() => {
      expect(patch).toHaveBeenCalledWith("daily_loss", {
        enabled: false, totp_code: "654321",
      });
    });
  });

  test("Enable on disabled switch PATCHes immediately, no TOTP", async () => {
    mockUser({ totp_configured: true });
    const list = defaultSwitchList();
    list.switches[0]!.enabled = false;
    vi.spyOn(apiMod.api, "meKillSwitches").mockResolvedValue(list);
    const patch = vi
      .spyOn(apiMod.api, "meKillSwitchPatch")
      .mockResolvedValue({
        name: "daily_loss", enabled: true, threshold_value: null,
        is_tripped: false, tripped_at: null, tripped_reason: null,
        default_threshold: 0.02,
      });

    render(<KillSwitches />);
    await screen.findByText("Daily loss");
    fireEvent.click(
      screen.getByRole("button", { name: /enable daily loss/i }),
    );
    await waitFor(() => {
      expect(patch).toHaveBeenCalledWith("daily_loss", {
        enabled: true, totp_code: undefined,
      });
    });
  });
});
