import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import {
  __resetCurrentUserForTests,
  useCurrentUser,
} from "@/hooks/useCurrentUser";
import { api, type MeOut } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  api: {
    me: vi.fn(),
  },
}));

const mockedApiMe = vi.mocked(api.me);

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
  mockedApiMe.mockReset();
});

afterEach(() => {
  __resetCurrentUserForTests();
});

describe("useCurrentUser", () => {
  test("initial render → isLoading=true, user=null", () => {
    mockedApiMe.mockReturnValue(new Promise(() => { /* pending */ }));
    const { result } = renderHook(() => useCurrentUser());
    expect(result.current.isLoading).toBe(true);
    expect(result.current.user).toBeNull();
    expect(result.current.isAdmin).toBe(false);
    expect(result.current.isImpersonating).toBe(false);
  });

  test("after fetch resolves → user populated, isLoading=false", async () => {
    mockedApiMe.mockResolvedValueOnce(meSample());
    const { result } = renderHook(() => useCurrentUser());
    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });
    expect(result.current.user?.email).toBe("user@example.com");
    expect(result.current.error).toBeNull();
  });

  test("isAdmin derives from user.is_admin", async () => {
    mockedApiMe.mockResolvedValueOnce(meSample({ is_admin: true }));
    const { result } = renderHook(() => useCurrentUser());
    await waitFor(() => {
      expect(result.current.user).not.toBeNull();
    });
    expect(result.current.isAdmin).toBe(true);
  });

  test("isImpersonating derives from user.is_impersonating", async () => {
    mockedApiMe.mockResolvedValueOnce(
      meSample({ is_admin: true, is_impersonating: true }),
    );
    const { result } = renderHook(() => useCurrentUser());
    await waitFor(() => {
      expect(result.current.user).not.toBeNull();
    });
    expect(result.current.isImpersonating).toBe(true);
  });

  test("multiple consumers share the same fetched user (cache)", async () => {
    mockedApiMe.mockResolvedValueOnce(meSample({ display_name: "Shared" }));
    const a = renderHook(() => useCurrentUser());
    const b = renderHook(() => useCurrentUser());
    await waitFor(() => {
      expect(a.result.current.user).not.toBeNull();
    });
    expect(b.result.current.user?.display_name).toBe("Shared");
    // Single fetch despite two consumers.
    expect(mockedApiMe).toHaveBeenCalledTimes(1);
  });

  test("error from /me sets error state, isLoading=false", async () => {
    mockedApiMe.mockRejectedValueOnce(new Error("HTTP 500 for /me"));
    const { result } = renderHook(() => useCurrentUser());
    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });
    expect(result.current.error?.message).toMatch(/HTTP 500/);
    expect(result.current.user).toBeNull();
  });

  test("reload() refetches and broadcasts new state", async () => {
    mockedApiMe.mockResolvedValueOnce(meSample({ display_name: "Old" }));
    const { result } = renderHook(() => useCurrentUser());
    await waitFor(() => {
      expect(result.current.user?.display_name).toBe("Old");
    });
    mockedApiMe.mockResolvedValueOnce(meSample({ display_name: "New" }));
    await act(async () => {
      await result.current.reload();
    });
    expect(result.current.user?.display_name).toBe("New");
    expect(mockedApiMe).toHaveBeenCalledTimes(2);
  });
});
