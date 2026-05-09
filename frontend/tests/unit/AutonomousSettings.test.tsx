/**
 * SP-8 Phase J — AutonomousSettings interactive tests.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { AutonomousSettings } from "@/tabs/Autonomous/panels/AutonomousSettings";
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

beforeEach(() => {
  vi.restoreAllMocks();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("AutonomousSettings", () => {
  test("Save is disabled when nothing has changed", () => {
    mockUser();
    render(<AutonomousSettings />);
    expect(
      screen.getByRole("button", { name: /save/i }),
    ).toBeDisabled();
  });

  test("editing a numeric field enables Save and PATCHes /me", async () => {
    mockUser();
    const spy = vi
      .spyOn(apiMod.api, "mePatch")
      .mockResolvedValue({
        ...baseUser,
        max_leverage_cap: 7,
      });

    render(<AutonomousSettings />);
    const lev = screen.getByLabelText(/max leverage/i) as HTMLInputElement;
    fireEvent.change(lev, { target: { value: "7" } });

    const save = screen.getByRole("button", { name: /save/i });
    expect(save).toBeEnabled();
    fireEvent.click(save);

    await waitFor(() => {
      expect(spy).toHaveBeenCalledWith({
        fixed_size_min_usdt: 30,
        fixed_size_max_usdt: 30,
        max_leverage_cap: 7,
      });
    });
  });

  test("invalid range (max < min) keeps Save disabled", () => {
    mockUser();
    render(<AutonomousSettings />);
    const min = screen.getByLabelText(/min size USDT/i) as HTMLInputElement;
    const max = screen.getByLabelText(/max size USDT/i) as HTMLInputElement;
    fireEvent.change(min, { target: { value: "100" } });
    fireEvent.change(max, { target: { value: "50" } });
    expect(
      screen.getByRole("button", { name: /save/i }),
    ).toBeDisabled();
  });

  test("API failure surfaces as alert", async () => {
    mockUser();
    vi.spyOn(apiMod.api, "mePatch").mockRejectedValue(new Error("boom"));
    render(<AutonomousSettings />);
    fireEvent.change(
      screen.getByLabelText(/max leverage/i),
      { target: { value: "5" } },
    );
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/boom/i);
    });
  });

  test("badges reflect TOTP + vault state from /me", () => {
    mockUser({ totp_configured: true, binance_keys_configured: true });
    render(<AutonomousSettings />);
    // Two "configured" badges appear.
    expect(screen.getAllByText(/^configured$/i)).toHaveLength(2);
  });
});
