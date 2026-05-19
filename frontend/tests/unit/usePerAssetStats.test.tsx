import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  api: { perAssetStats: vi.fn() },
}));

import { api } from "@/lib/api";
import { usePerAssetStats } from "@/tabs/BotStatus/hooks/usePerAssetStats";

beforeEach(() => {
  vi.mocked(api.perAssetStats).mockReset();
  vi.mocked(api.perAssetStats).mockResolvedValue([]);
});

afterEach(() => {
  vi.clearAllMocks();
  vi.useRealTimers();
});

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("usePerAssetStats", () => {
  test("polls at the configured interval", async () => {
    vi.useFakeTimers();
    renderHook(() => usePerAssetStats(30, 5000));
    await flush();
    expect(api.perAssetStats).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(5000);
    });
    await flush();
    expect(api.perAssetStats).toHaveBeenCalledTimes(2);

    await act(async () => {
      vi.advanceTimersByTime(5000);
    });
    await flush();
    expect(api.perAssetStats).toHaveBeenCalledTimes(3);
  });

  test("does not poll when interval is 0", async () => {
    vi.useFakeTimers();
    renderHook(() => usePerAssetStats(30, 0));
    await flush();
    expect(api.perAssetStats).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(120_000);
    });
    await flush();
    expect(api.perAssetStats).toHaveBeenCalledTimes(1);
  });

  test("cleanup cancels pending interval on unmount", async () => {
    vi.useFakeTimers();
    const { unmount } = renderHook(() => usePerAssetStats(30, 5000));
    await flush();
    expect(api.perAssetStats).toHaveBeenCalledTimes(1);

    unmount();
    await act(async () => {
      vi.advanceTimersByTime(60_000);
    });
    expect(api.perAssetStats).toHaveBeenCalledTimes(1);
  });
});
