import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { Profile } from "@/tabs/Settings/Profile";
import { __resetCurrentUserForTests } from "@/hooks/useCurrentUser";
import type { MeOut } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  api: {
    me: vi.fn(),
    mePatch: vi.fn(),
  },
}));

import { api } from "@/lib/api";
const mockedMe = vi.mocked(api.me);
const mockedPatch = vi.mocked(api.mePatch);

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
  mockedPatch.mockReset();
});

afterEach(() => {
  __resetCurrentUserForTests();
  vi.clearAllMocks();
});

describe("Settings/Profile", () => {
  test("seeds form fields from /me on mount", async () => {
    mockedMe.mockResolvedValueOnce(
      meSample({
        display_name: "Alice",
        quiet_hours_enabled: true,
        quiet_hours_start: "22:00",
        quiet_hours_end: "07:00",
      }),
    );
    render(<Profile />);
    await waitFor(() => {
      expect(screen.getByLabelText(/display name/i)).toHaveValue("Alice");
    });
    expect(screen.getByLabelText(/start/i)).toHaveValue("22:00");
    expect(screen.getByLabelText(/end/i)).toHaveValue("07:00");
    expect(screen.getByLabelText(/enable quiet hours/i)).toBeChecked();
  });

  test("shows the email read-only", async () => {
    mockedMe.mockResolvedValueOnce(meSample({ email: "show@me.com" }));
    render(<Profile />);
    await waitFor(() => {
      expect(screen.getByText("show@me.com")).toBeInTheDocument();
    });
  });

  test("typing display_name + Save calls api.mePatch with new value", async () => {
    mockedMe.mockResolvedValueOnce(meSample({ display_name: "Old" }));
    mockedPatch.mockResolvedValueOnce(meSample({ display_name: "Brand New" }));
    mockedMe.mockResolvedValueOnce(meSample({ display_name: "Brand New" }));
    render(<Profile />);
    await waitFor(() => {
      expect(screen.getByLabelText(/display name/i)).toHaveValue("Old");
    });
    fireEvent.change(screen.getByLabelText(/display name/i), {
      target: { value: "Brand New" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
    await waitFor(() => {
      expect(mockedPatch).toHaveBeenCalledTimes(1);
    });
    const call = mockedPatch.mock.calls[0];
    expect(call?.[0]).toMatchObject({ display_name: "Brand New" });
  });

  test("Save shows success status when API resolves", async () => {
    mockedMe.mockResolvedValueOnce(meSample());
    mockedPatch.mockResolvedValueOnce(meSample());
    mockedMe.mockResolvedValueOnce(meSample());
    render(<Profile />);
    await waitFor(() => {
      expect(screen.getByLabelText(/display name/i)).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
    await waitFor(() => {
      expect(screen.getByRole("status")).toHaveTextContent(/saved/i);
    });
  });

  test("Save shows error alert when API rejects", async () => {
    mockedMe.mockResolvedValueOnce(meSample());
    mockedPatch.mockRejectedValueOnce(new Error("HTTP 403 for /me"));
    render(<Profile />);
    await waitFor(() => {
      expect(screen.getByLabelText(/display name/i)).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/HTTP 403/);
    });
  });

  test("toggling quiet_hours_enabled and Save sends new flag", async () => {
    mockedMe.mockResolvedValueOnce(meSample({ quiet_hours_enabled: false }));
    mockedPatch.mockResolvedValueOnce(meSample({ quiet_hours_enabled: true }));
    mockedMe.mockResolvedValueOnce(meSample({ quiet_hours_enabled: true }));
    render(<Profile />);
    await waitFor(() => {
      expect(screen.getByLabelText(/enable quiet hours/i)).toBeInTheDocument();
    });
    fireEvent.click(screen.getByLabelText(/enable quiet hours/i));
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
    await waitFor(() => {
      expect(mockedPatch).toHaveBeenCalled();
    });
    const call = mockedPatch.mock.calls[0];
    expect(call?.[0]?.quiet_hours_enabled).toBe(true);
  });
});
