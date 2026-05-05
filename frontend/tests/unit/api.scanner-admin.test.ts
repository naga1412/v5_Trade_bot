import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { api } from "@/lib/api";

const BASE = "/api/v1";

function jsonResponse(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as unknown as Response;
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("api.scannerRadar", () => {
  test("default args -> /scanner/radar?market=crypto&tf=1h&limit=200", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({
      scanned_at: "2026-05-05T00:00:00Z",
      market: "crypto", timeframe: "1h",
      scanned_count: 0,
      filter_counts: { all: 0, confirmed: 0, probable: 0, weak: 0, diverging: 0 },
      supervisor_progress: { done: 0, total: 8 },
      bullish: [], bearish: [],
    }));
    await api.scannerRadar();
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      `${BASE}/scanner/radar?market=crypto&tf=1h&limit=200`,
    );
  });

  test("custom args -> URL params reflect overrides", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({
      scanned_at: "x", market: "stock", timeframe: "1d",
      scanned_count: 0,
      filter_counts: { all: 0, confirmed: 0, probable: 0, weak: 0, diverging: 0 },
      supervisor_progress: { done: 0, total: 8 },
      bullish: [], bearish: [],
    }));
    await api.scannerRadar({ market: "stock", tf: "1d", limit: 50 });
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      `${BASE}/scanner/radar?market=stock&tf=1d&limit=50`,
    );
  });
});

describe("api admin patterns / adapters / traps helpers", () => {
  test("adminListPatterns hits /admin/patterns", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse([]));
    await api.adminListPatterns();
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(`${BASE}/admin/patterns`);
  });

  test("adminTogglePattern POST .../disable when enable=false", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({}));
    await api.adminTogglePattern("doji", false, "noisy");
    const call = fetchMock.mock.calls[0]!;
    expect(String(call[0])).toBe(`${BASE}/admin/patterns/doji/disable`);
    expect((call[1] as RequestInit).method).toBe("POST");
  });

  test("adminTogglePattern POST .../enable when enable=true", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({}));
    await api.adminTogglePattern("doji", true);
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      `${BASE}/admin/patterns/doji/enable`,
    );
  });

  test("adminListAdapters hits /admin/adapters/health", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse([]));
    await api.adminListAdapters();
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      `${BASE}/admin/adapters/health`,
    );
  });

  test("adminSyncAdapter POSTs /admin/adapters/{ex}/sync", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({
      exchange: "binance", added: 0, still_active: 0, newly_delisted: 0,
    }));
    await api.adminSyncAdapter("binance");
    const call = fetchMock.mock.calls[0]!;
    expect(String(call[0])).toBe(`${BASE}/admin/adapters/binance/sync`);
    expect((call[1] as RequestInit).method).toBe("POST");
  });

  test("adminListTraps hits /admin/traps", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse([]));
    await api.adminListTraps();
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(`${BASE}/admin/traps`);
  });

  test("adminToggleTrap POST .../disable when enable=false", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({}));
    await api.adminToggleTrap("liquidity_sweep", false, "");
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      `${BASE}/admin/traps/liquidity_sweep/disable`,
    );
  });
});
