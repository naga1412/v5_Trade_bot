import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { Trading } from "@/tabs/Settings/Trading";
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

beforeEach(() => {
  __resetCurrentUserForTests();
  mockedMe.mockReset();
  mockedPatch.mockReset();
});

afterEach(() => {
  __resetCurrentUserForTests();
  vi.clearAllMocks();
});

describe("Settings/Trading", () => {
  test("seeds position sizing fields from /me", async () => {
    mockedMe.mockResolvedValueOnce(meSample());
    render(<Trading />);
    await waitFor(() => {
      expect(screen.getByLabelText(/fixed size min/i)).toHaveValue(20);
    });
    expect(screen.getByLabelText(/fixed size max/i)).toHaveValue(50);
    expect(screen.getByLabelText(/max concurrent positions/i)).toHaveValue(5);
    expect(screen.getByLabelText(/max leverage cap/i)).toHaveValue(3);
  });

  test("trading mode is read-only and shows the current mode value", async () => {
    mockedMe.mockResolvedValueOnce(meSample({ trading_mode: "manual" }));
    render(<Trading />);
    await waitFor(() => {
      expect(screen.getByTestId("trading-mode-readonly")).toHaveTextContent("manual");
    });
    // No mode <select> or <input> means the field is genuinely read-only.
    expect(screen.queryByLabelText(/^mode$/i)).toBeNull();
  });

  test("Save calls api.mePatch with current numeric values", async () => {
    mockedMe.mockResolvedValueOnce(meSample());
    mockedPatch.mockResolvedValueOnce(meSample({ fixed_size_min_usdt: 30 }));
    mockedMe.mockResolvedValueOnce(meSample({ fixed_size_min_usdt: 30 }));
    render(<Trading />);
    await waitFor(() => {
      expect(screen.getByLabelText(/fixed size min/i)).toHaveValue(20);
    });
    fireEvent.change(screen.getByLabelText(/fixed size min/i), {
      target: { value: "30" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
    await waitFor(() => {
      expect(mockedPatch).toHaveBeenCalled();
    });
    const call = mockedPatch.mock.calls[0];
    expect(call?.[0]).toMatchObject({
      fixed_size_min_usdt: 30,
      fixed_size_max_usdt: 50,
      max_concurrent_positions: 5,
      max_leverage_cap: 3,
    });
  });

  test("clearing a field sends null in the PATCH body", async () => {
    mockedMe.mockResolvedValueOnce(meSample());
    mockedPatch.mockResolvedValueOnce(meSample({ max_leverage_cap: null }));
    mockedMe.mockResolvedValueOnce(meSample({ max_leverage_cap: null }));
    render(<Trading />);
    await waitFor(() => {
      expect(screen.getByLabelText(/max leverage cap/i)).toBeInTheDocument();
    });
    fireEvent.change(screen.getByLabelText(/max leverage cap/i), {
      target: { value: "" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
    await waitFor(() => {
      expect(mockedPatch).toHaveBeenCalled();
    });
    expect(mockedPatch.mock.calls[0]?.[0]?.max_leverage_cap).toBeNull();
  });

  test("renders Kill switches placeholder card", async () => {
    mockedMe.mockResolvedValueOnce(meSample());
    render(<Trading />);
    await waitFor(() => {
      expect(screen.getByText(/coming in sp-8/i)).toBeInTheDocument();
    });
  });
});
