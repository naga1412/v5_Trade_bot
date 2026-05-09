/**
 * SP-8 Phase J — TotpSetupWizard interactive tests.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { TotpSetupWizard } from "@/tabs/Autonomous/panels/TotpSetupWizard";
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

const SETUP_RESP = {
  provisioning_uri: "otpauth://totp/trading-radar:u@x.com?secret=ABC&issuer=tr",
  secret_for_display: "ABCDEFGH",
  backup_codes: ["aaaa-1111", "bbbb-2222", "cccc-3333"],
};

beforeEach(() => {
  vi.restoreAllMocks();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("TotpSetupWizard", () => {
  test("idle state shows 'not set up' + 'Set up TOTP' button", () => {
    mockUser({ totp_configured: false });
    render(<TotpSetupWizard />);
    expect(screen.getByText(/not set up/i)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /set up totp/i }),
    ).toBeInTheDocument();
  });

  test("idle state shows 'configured' + 'Re-run setup' when already set", () => {
    mockUser({ totp_configured: true });
    render(<TotpSetupWizard />);
    expect(screen.getByText(/configured ✓/i)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /re-run setup/i }),
    ).toBeInTheDocument();
  });

  test("re-run setup shows overwrite confirm before regenerating", async () => {
    mockUser({ totp_configured: true });
    const spy = vi.spyOn(apiMod.api, "meTotpSetup");

    render(<TotpSetupWizard />);
    fireEvent.click(screen.getByRole("button", { name: /re-run setup/i }));
    expect(screen.getByRole("alert")).toHaveTextContent(/invalidates/i);
    expect(spy).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /yes, regenerate/i }));
    await waitFor(() => {
      expect(spy).toHaveBeenCalledTimes(1);
    });
  });

  test("setup loads + renders QR + backup codes + secret", async () => {
    mockUser({ totp_configured: false });
    vi.spyOn(apiMod.api, "meTotpSetup").mockResolvedValue(SETUP_RESP);
    render(<TotpSetupWizard />);
    fireEvent.click(screen.getByRole("button", { name: /set up totp/i }));
    expect(
      await screen.findByLabelText(/totp provisioning qr/i),
    ).toBeInTheDocument();
    for (const c of SETUP_RESP.backup_codes) {
      expect(screen.getByText(c)).toBeInTheDocument();
    }
    // Manual URI is in a collapsed details element — open it.
    fireEvent.click(screen.getByText(/paste the URI manually/i));
    expect(screen.getByText(SETUP_RESP.secret_for_display)).toBeInTheDocument();
  });

  test("verify success transitions to 'done' state + reloads /me", async () => {
    const reload = vi.fn().mockResolvedValue(undefined);
    vi.spyOn(currentUserHook, "useCurrentUser").mockReturnValue({
      user: { ...baseUser, totp_configured: false },
      isAdmin: false,
      isImpersonating: false,
      isLoading: false,
      error: null,
      reload,
    });
    vi.spyOn(apiMod.api, "meTotpSetup").mockResolvedValue(SETUP_RESP);
    vi.spyOn(apiMod.api, "meTotpVerify").mockResolvedValue({ ok: true });

    render(<TotpSetupWizard />);
    fireEvent.click(screen.getByRole("button", { name: /set up totp/i }));
    await screen.findByLabelText(/totp verification code/i);

    fireEvent.change(
      screen.getByLabelText(/totp verification code/i),
      { target: { value: "123456" } },
    );
    fireEvent.click(screen.getByRole("button", { name: /^verify$/i }));

    await waitFor(() => {
      expect(reload).toHaveBeenCalled();
    });
    expect(
      await screen.findByRole("status"),
    ).toHaveTextContent(/totp verified/i);
  });

  test("verify with bad code shows alert, stays on verify stage", async () => {
    mockUser({ totp_configured: false });
    vi.spyOn(apiMod.api, "meTotpSetup").mockResolvedValue(SETUP_RESP);
    vi.spyOn(apiMod.api, "meTotpVerify").mockResolvedValue({ ok: false });

    render(<TotpSetupWizard />);
    fireEvent.click(screen.getByRole("button", { name: /set up totp/i }));
    await screen.findByLabelText(/totp verification code/i);

    fireEvent.change(
      screen.getByLabelText(/totp verification code/i),
      { target: { value: "000000" } },
    );
    fireEvent.click(screen.getByRole("button", { name: /^verify$/i }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/invalid code/i);
    });
    // Still on verify stage — input still present.
    expect(
      screen.getByLabelText(/totp verification code/i),
    ).toBeInTheDocument();
  });

  test("Cancel returns to idle without verifying", async () => {
    mockUser({ totp_configured: false });
    vi.spyOn(apiMod.api, "meTotpSetup").mockResolvedValue(SETUP_RESP);
    render(<TotpSetupWizard />);
    fireEvent.click(screen.getByRole("button", { name: /set up totp/i }));
    await screen.findByLabelText(/totp verification code/i);
    fireEvent.click(screen.getByRole("button", { name: /^cancel$/i }));
    expect(
      screen.getByRole("button", { name: /set up totp/i }),
    ).toBeInTheDocument();
  });
});
