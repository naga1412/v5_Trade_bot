/**
 * SP-8 Phase J — ModeSwitcher interactive tests.
 *
 * Three flows:
 *  1. Downgrade fires immediately (no TOTP modal).
 *  2. Upgrade without TOTP configured shows an error, does NOT call API.
 *  3. Upgrade with TOTP configured opens the modal, accepts a 6-digit
 *     code, and PATCHes /me/trading-mode with totp_code.
 *  4. Backend returns 400 → error renders.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { ModeSwitcher } from "@/tabs/Autonomous/panels/ModeSwitcher";
import * as currentUserHook from "@/hooks/useCurrentUser";
import * as apiMod from "@/lib/api";

const baseUser = {
  id: 1,
  email: "u@x.com",
  display_name: "u",
  is_admin: false,
  is_impersonating: false,
  trading_mode: "manual" as "manual" | "telegram-approve" | "fully-auto",
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

function mockCurrentUser(overrides: Partial<typeof baseUser> = {}) {
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

describe("ModeSwitcher", () => {
  test("downgrade fires immediately without TOTP modal", async () => {
    mockCurrentUser({ trading_mode: "telegram-approve", totp_configured: true });
    const spy = vi
      .spyOn(apiMod.api, "meChangeTradingMode")
      .mockResolvedValue({
        new_mode: "manual",
        old_mode: "telegram-approve",
        audit_row_hash: "h1",
        is_upgrade: false,
      });

    render(<ModeSwitcher />);
    fireEvent.click(screen.getByRole("button", { name: /^manual$/i }));

    await waitFor(() => {
      expect(spy).toHaveBeenCalledWith({ new_mode: "manual" });
    });
    // No TOTP form should appear.
    expect(
      screen.queryByLabelText(/totp code/i),
    ).not.toBeInTheDocument();
  });

  test("upgrade without TOTP configured shows error, no API call", async () => {
    mockCurrentUser({ trading_mode: "manual", totp_configured: false });
    const spy = vi.spyOn(apiMod.api, "meChangeTradingMode");

    render(<ModeSwitcher />);
    fireEvent.click(
      screen.getByRole("button", { name: /telegram approve/i }),
    );

    expect(
      screen.getByRole("alert"),
    ).toHaveTextContent(/configure TOTP/i);
    expect(spy).not.toHaveBeenCalled();
  });

  test("upgrade with TOTP configured opens modal then PATCHes with code", async () => {
    mockCurrentUser({ trading_mode: "manual", totp_configured: true });
    const spy = vi
      .spyOn(apiMod.api, "meChangeTradingMode")
      .mockResolvedValue({
        new_mode: "telegram-approve",
        old_mode: "manual",
        audit_row_hash: "h2",
        is_upgrade: true,
      });

    render(<ModeSwitcher />);
    fireEvent.click(
      screen.getByRole("button", { name: /telegram approve/i }),
    );
    // TOTP form appears.
    const input = screen.getByLabelText(/totp code/i) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: /confirm/i }));

    await waitFor(() => {
      expect(spy).toHaveBeenCalledWith({
        new_mode: "telegram-approve",
        totp_code: "123456",
      });
    });
  });

  test("API error surfaces as alert text", async () => {
    mockCurrentUser({ trading_mode: "telegram-approve", totp_configured: true });
    vi.spyOn(apiMod.api, "meChangeTradingMode").mockRejectedValue(
      new Error("network down"),
    );

    render(<ModeSwitcher />);
    fireEvent.click(screen.getByRole("button", { name: /^manual$/i }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/network down/i);
    });
  });

  test("clicking the active mode is a no-op", () => {
    mockCurrentUser({ trading_mode: "manual" });
    const spy = vi.spyOn(apiMod.api, "meChangeTradingMode");

    render(<ModeSwitcher />);
    fireEvent.click(screen.getByRole("button", { name: /^manual$/i }));
    expect(spy).not.toHaveBeenCalled();
  });
});
