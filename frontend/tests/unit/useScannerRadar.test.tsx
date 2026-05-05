import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import type { ScannerRadar } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  api: { scannerRadar: vi.fn() },
}));

import { api } from "@/lib/api";
import { useScannerRadar } from "@/tabs/Tab3Scanner/hooks/useScannerRadar";

const sample: ScannerRadar = {
  scanned_at: "2026-05-03T00:00:00Z",
  market: "crypto",
  timeframe: "1h",
  scanned_count: 42,
  filter_counts: { all: 42, confirmed: 5, probable: 12, weak: 25, diverging: 3 },
  supervisor_progress: { done: 2, total: 8 },
  bullish: [],
  bearish: [],
};

beforeEach(() => {
  vi.mocked(api.scannerRadar).mockReset();
  vi.mocked(api.scannerRadar).mockResolvedValue(sample);
});

afterEach(() => {
  vi.clearAllMocks();
  vi.useRealTimers();
});

async function flush() {
  // Allow any pending microtasks (the awaited fetch + setState) to resolve.
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("useScannerRadar", () => {
  test("fetches once on mount and exposes data", async () => {
    const { result } = renderHook(() =>
      useScannerRadar({ market: "crypto", tf: "1h", limit: 50 }),
    );
    await flush();
    expect(api.scannerRadar).toHaveBeenCalledTimes(1);
    expect(vi.mocked(api.scannerRadar).mock.calls[0]?.[0]).toMatchObject({
      market: "crypto", tf: "1h", limit: 50,
    });
    expect(result.current.data).not.toBeNull();
    expect(result.current.error).toBeNull();
  });

  test("refetches when interval elapses", async () => {
    vi.useFakeTimers();
    renderHook(() =>
      useScannerRadar({
        market: "crypto", tf: "1h", limit: 50, refreshIntervalMs: 120_000,
      }),
    );
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(api.scannerRadar).toHaveBeenCalledTimes(1);
    await act(async () => {
      vi.advanceTimersByTime(120_000);
    });
    await act(async () => { await Promise.resolve(); });
    expect(api.scannerRadar).toHaveBeenCalledTimes(2);
  });

  test("manual refetch triggers an immediate fetch", async () => {
    const { result } = renderHook(() =>
      useScannerRadar({ market: "crypto", tf: "1h", limit: 50 }),
    );
    await flush();
    expect(api.scannerRadar).toHaveBeenCalledTimes(1);
    await act(async () => { await result.current.refetch(); });
    expect(api.scannerRadar).toHaveBeenCalledTimes(2);
  });

  test("captures error when api rejects", async () => {
    vi.mocked(api.scannerRadar).mockRejectedValueOnce(new Error("boom"));
    const { result } = renderHook(() =>
      useScannerRadar({ market: "crypto", tf: "1h", limit: 50 }),
    );
    await flush();
    expect(result.current.error).not.toBeNull();
    expect(result.current.error?.message).toBe("boom");
  });

  test("cleanup cancels pending interval", async () => {
    vi.useFakeTimers();
    const { unmount } = renderHook(() =>
      useScannerRadar({
        market: "crypto", tf: "1h", limit: 50, refreshIntervalMs: 60_000,
      }),
    );
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(api.scannerRadar).toHaveBeenCalledTimes(1);
    unmount();
    await act(async () => {
      vi.advanceTimersByTime(180_000);
    });
    expect(api.scannerRadar).toHaveBeenCalledTimes(1);
  });
});
