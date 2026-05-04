import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { Secrets } from "@/tabs/Settings/Secrets";
import { __resetCurrentUserForTests } from "@/hooks/useCurrentUser";
import type { MeOut, TotpSetupOut } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  api: {
    me: vi.fn(),
    meBinanceKeys: vi.fn(),
    meTelegram: vi.fn(),
    meTotpSetup: vi.fn(),
    meTotpVerify: vi.fn(),
  },
}));

import { api } from "@/lib/api";
const mockedMe = vi.mocked(api.me);
const mockedBinance = vi.mocked(api.meBinanceKeys);
const mockedTelegram = vi.mocked(api.meTelegram);
const mockedTotpSetup = vi.mocked(api.meTotpSetup);
const mockedTotpVerify = vi.mocked(api.meTotpVerify);

function meSample(overrides: Partial<MeOut> = {}): MeOut {
  return {
    id: 1,
    email: "u@e.com",
    display_name: "U",
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

function totpSample(): TotpSetupOut {
  return {
    provisioning_uri: "otpauth://totp/Trading%20Radar:u@e.com?secret=ABC&issuer=Trading%20Radar",
    secret_for_display: "ABCDEFGHIJKLMNOP",
    backup_codes: ["a1b2c3", "d4e5f6", "g7h8i9", "j0k1l2"],
  };
}

beforeEach(() => {
  __resetCurrentUserForTests();
  mockedMe.mockReset();
  mockedBinance.mockReset();
  mockedTelegram.mockReset();
  mockedTotpSetup.mockReset();
  mockedTotpVerify.mockReset();
});

afterEach(() => {
  __resetCurrentUserForTests();
  vi.clearAllMocks();
});

describe("Settings/Secrets", () => {
  test("renders Configured badge when has_*=true", async () => {
    mockedMe.mockResolvedValueOnce(
      meSample({
        binance_keys_configured: true,
        telegram_configured: true,
        totp_configured: true,
      }),
    );
    render(<Secrets />);
    await waitFor(() => {
      expect(screen.getAllByTestId("configured-badge").length).toBeGreaterThanOrEqual(3);
    });
  });

  test("renders Not configured badge when has_*=false", async () => {
    mockedMe.mockResolvedValueOnce(meSample());
    render(<Secrets />);
    await waitFor(() => {
      expect(screen.getAllByTestId("not-configured-badge").length).toBe(3);
    });
  });

  test("submitting Binance keys calls api.meBinanceKeys with both fields", async () => {
    mockedMe.mockResolvedValueOnce(meSample());
    mockedBinance.mockResolvedValueOnce(undefined);
    mockedMe.mockResolvedValueOnce(meSample({ binance_keys_configured: true }));
    render(<Secrets />);
    await waitFor(() => {
      expect(screen.getByLabelText(/api key/i)).toBeInTheDocument();
    });
    fireEvent.change(screen.getByLabelText(/api key/i), {
      target: { value: "MYKEY" },
    });
    fireEvent.change(screen.getByLabelText(/api secret/i), {
      target: { value: "MYSECRET" },
    });
    fireEvent.click(screen.getByRole("button", { name: /save binance keys/i }));
    await waitFor(() => {
      expect(mockedBinance).toHaveBeenCalledWith("MYKEY", "MYSECRET");
    });
  });

  test("submitting Telegram form calls api.meTelegram", async () => {
    mockedMe.mockResolvedValueOnce(meSample());
    mockedTelegram.mockResolvedValueOnce(undefined);
    mockedMe.mockResolvedValueOnce(meSample({ telegram_configured: true }));
    render(<Secrets />);
    await waitFor(() => {
      expect(screen.getByLabelText(/bot token/i)).toBeInTheDocument();
    });
    fireEvent.change(screen.getByLabelText(/bot token/i), {
      target: { value: "bot:abc" },
    });
    fireEvent.change(screen.getByLabelText(/chat id/i), {
      target: { value: "12345" },
    });
    fireEvent.click(screen.getByRole("button", { name: /save telegram/i }));
    await waitFor(() => {
      expect(mockedTelegram).toHaveBeenCalledWith("bot:abc", "12345");
    });
  });

  test("clicking Set up TOTP fetches setup and renders provisioning URI + backup codes", async () => {
    mockedMe.mockResolvedValueOnce(meSample());
    mockedTotpSetup.mockResolvedValueOnce(totpSample());
    render(<Secrets />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /set up totp/i })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /set up totp/i }));
    await waitFor(() => {
      expect(screen.getByTestId("totp-setup-panel")).toBeInTheDocument();
    });
    expect(screen.getByTestId("totp-provisioning-uri")).toHaveTextContent(/otpauth/);
    expect(screen.getByTestId("totp-secret-display")).toHaveTextContent("ABCDEFGHIJKLMNOP");
    const codes = screen.getByTestId("totp-backup-codes");
    expect(codes).toHaveTextContent("a1b2c3");
    expect(codes).toHaveTextContent("j0k1l2");
    expect(screen.getByRole("alert")).toHaveTextContent(/save these now/i);
  });

  test("after setup, entering a 6-digit code and submit calls api.meTotpVerify", async () => {
    mockedMe.mockResolvedValueOnce(meSample());
    mockedTotpSetup.mockResolvedValueOnce(totpSample());
    mockedTotpVerify.mockResolvedValueOnce({ ok: true });
    mockedMe.mockResolvedValueOnce(meSample({ totp_configured: true }));
    render(<Secrets />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /set up totp/i })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /set up totp/i }));
    await waitFor(() => {
      expect(screen.getByTestId("totp-setup-panel")).toBeInTheDocument();
    });
    fireEvent.change(screen.getByLabelText(/6-digit code/i), {
      target: { value: "123456" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^verify$/i }));
    await waitFor(() => {
      expect(mockedTotpVerify).toHaveBeenCalledWith("123456");
    });
  });

  test("verify with ok=false shows error and stays on setup panel", async () => {
    mockedMe.mockResolvedValueOnce(meSample());
    mockedTotpSetup.mockResolvedValueOnce(totpSample());
    mockedTotpVerify.mockResolvedValueOnce({ ok: false });
    render(<Secrets />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /set up totp/i })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /set up totp/i }));
    await waitFor(() => {
      expect(screen.getByTestId("totp-setup-panel")).toBeInTheDocument();
    });
    fireEvent.change(screen.getByLabelText(/6-digit code/i), {
      target: { value: "999999" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^verify$/i }));
    await waitFor(() => {
      expect(
        screen.getByText(/invalid totp code/i),
      ).toBeInTheDocument();
    });
    // Setup panel still mounted -> backup codes still visible.
    expect(screen.getByTestId("totp-setup-panel")).toBeInTheDocument();
  });

  test("API error during binance save renders alert", async () => {
    mockedMe.mockResolvedValueOnce(meSample());
    mockedBinance.mockRejectedValueOnce(new Error("HTTP 403 for /me/binance-keys"));
    render(<Secrets />);
    await waitFor(() => {
      expect(screen.getByLabelText(/api key/i)).toBeInTheDocument();
    });
    fireEvent.change(screen.getByLabelText(/api key/i), { target: { value: "K" } });
    fireEvent.change(screen.getByLabelText(/api secret/i), { target: { value: "S" } });
    fireEvent.click(screen.getByRole("button", { name: /save binance keys/i }));
    await waitFor(() => {
      const alerts = screen.getAllByRole("alert");
      expect(alerts.some((a) => /HTTP 403/.test(a.textContent ?? ""))).toBe(true);
    });
  });
});
