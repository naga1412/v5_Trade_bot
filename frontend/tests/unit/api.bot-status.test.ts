import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import {
  api,
  type AssetUniverse,
  type BotOverview,
  type EquityCurve,
  type LongShortBreakdown,
  type OpenPosition,
  type PerAssetStat,
  type PromotionGate,
  type RecentTrade,
} from "@/lib/api";

const BASE = "/api/v1";

function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    json: async () => body,
  } as unknown as Response;
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function lastUrl(): string {
  const call = fetchMock.mock.calls[0];
  if (!call) throw new Error("fetch was not called");
  return String(call[0]);
}

describe("api.botOverview", () => {
  test("calls correct URL and returns parsed BotOverview", async () => {
    const sample: BotOverview = {
      last_24h: {
        window: "24h",
        trades: 3,
        pnl_usdt: 1.2,
        pnl_pct: 0.5,
        win_rate: 0.66,
        sharpe_annualized: 1.1,
        max_drawdown: 0.04,
        profit_factor: 1.8,
      },
      last_7d: {
        window: "7d",
        trades: 12,
        pnl_usdt: 5.5,
        pnl_pct: 2.0,
        win_rate: 0.58,
        sharpe_annualized: 0.9,
        max_drawdown: 0.08,
        profit_factor: 1.4,
      },
      last_30d: {
        window: "30d",
        trades: 50,
        pnl_usdt: 22.0,
        pnl_pct: 5.0,
        win_rate: 0.55,
        sharpe_annualized: 1.0,
        max_drawdown: 0.12,
        profit_factor: 1.5,
      },
      long_only_30d: {
        window: "30d",
        trades: 30,
        pnl_usdt: 15.0,
        pnl_pct: 3.5,
        win_rate: 0.6,
        sharpe_annualized: 1.1,
        max_drawdown: 0.07,
        profit_factor: 1.6,
      },
      short_only_30d: {
        window: "30d",
        trades: 20,
        pnl_usdt: 7.0,
        pnl_pct: 1.5,
        win_rate: 0.5,
        sharpe_annualized: 0.8,
        max_drawdown: 0.1,
        profit_factor: 1.3,
      },
    };
    fetchMock.mockResolvedValueOnce(jsonResponse(sample));
    const result = await api.botOverview();
    expect(lastUrl()).toBe(`${BASE}/bot-status/overview`);
    expect(result.last_24h.trades).toBe(3);
    expect(result.short_only_30d.window).toBe("30d");
  });
});

describe("api.promotionGate", () => {
  test("calls correct URL and returns parsed PromotionGate", async () => {
    const sample: PromotionGate = {
      target_mode: "telegram-approve",
      metrics: [
        { name: "win_rate", current: 0.55, threshold: 0.5, operator: ">=", passing: true },
      ],
      all_passing: true,
      distance_summary: "all good",
    };
    fetchMock.mockResolvedValueOnce(jsonResponse(sample));
    const result = await api.promotionGate();
    expect(lastUrl()).toBe(`${BASE}/bot-status/promotion-gate`);
    expect(result.metrics[0]?.name).toBe("win_rate");
    expect(result.all_passing).toBe(true);
  });
});

describe("api.openPositions", () => {
  test("calls correct URL and returns parsed OpenPosition[]", async () => {
    const sample: OpenPosition[] = [
      {
        symbol: "BTC/USDT",
        direction: "LONG",
        entry_price: 100,
        stop_loss: 95,
        take_profit: 110,
        position_size_usdt: 1000,
        bars_held: 4,
        opened_at: "2026-05-01T12:00:00Z",
        signal_id: "sig-1",
        current_price: 105,
        unrealized_pnl_pct: 5,
        unrealized_pnl_usdt: 50,
      },
    ];
    fetchMock.mockResolvedValueOnce(jsonResponse(sample));
    const result = await api.openPositions();
    expect(lastUrl()).toBe(`${BASE}/bot-status/open-positions`);
    expect(result).toHaveLength(1);
    expect(result[0]?.symbol).toBe("BTC/USDT");
  });
});

describe("api.perAssetStats", () => {
  test("uses default days=30 when not specified", async () => {
    const sample: PerAssetStat[] = [
      { symbol: "BTC/USDT", trades: 5, win_rate: 0.6, avg_rr: 2.0, pnl_usdt: 10, sharpe_annualized: 1.0 },
    ];
    fetchMock.mockResolvedValueOnce(jsonResponse(sample));
    const result = await api.perAssetStats();
    expect(lastUrl()).toBe(`${BASE}/bot-status/per-asset?days=30`);
    expect(result[0]?.symbol).toBe("BTC/USDT");
  });

  test("respects custom days argument", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse([]));
    await api.perAssetStats(7);
    expect(lastUrl()).toBe(`${BASE}/bot-status/per-asset?days=7`);
  });
});

describe("api.recentTrades", () => {
  test("no filters → bare URL", async () => {
    const sample: RecentTrade[] = [];
    fetchMock.mockResolvedValueOnce(jsonResponse(sample));
    const result = await api.recentTrades();
    expect(lastUrl()).toBe(`${BASE}/bot-status/recent-trades`);
    expect(result).toEqual([]);
  });

  test("limit only", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse([]));
    await api.recentTrades({ limit: 25 });
    expect(lastUrl()).toBe(`${BASE}/bot-status/recent-trades?limit=25`);
  });

  test("symbol only — encodes BTC/USDT", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse([]));
    await api.recentTrades({ symbol: "BTC/USDT" });
    // URLSearchParams encodes "/" as %2F
    expect(lastUrl()).toBe(`${BASE}/bot-status/recent-trades?symbol=BTC%2FUSDT`);
  });

  test("all filters together", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse([]));
    await api.recentTrades({ limit: 10, symbol: "ETH/USDT", direction: "SHORT", result: "win" });
    const url = lastUrl();
    expect(url.startsWith(`${BASE}/bot-status/recent-trades?`)).toBe(true);
    expect(url).toContain("limit=10");
    expect(url).toContain("symbol=ETH%2FUSDT");
    expect(url).toContain("direction=SHORT");
    expect(url).toContain("result=win");
  });

  test("returns parsed RecentTrade[]", async () => {
    const sample: RecentTrade[] = [
      {
        closed_at: "2026-05-01T13:00:00Z",
        symbol: "BTC/USDT",
        direction: "LONG",
        entry_price: 100,
        exit_price: 110,
        pnl_pct: 10,
        pnl_usdt: 100,
        exit_reason: "TAKE_PROFIT",
        bars_held: 8,
        signal_id: "sig-1",
      },
    ];
    fetchMock.mockResolvedValueOnce(jsonResponse(sample));
    const result = await api.recentTrades({ limit: 1 });
    expect(result[0]?.exit_reason).toBe("TAKE_PROFIT");
  });
});

describe("api.longShort", () => {
  test("uses default days=30", async () => {
    const sample: LongShortBreakdown = {
      long: {
        window: "30d", trades: 5, pnl_usdt: 10, pnl_pct: 1, win_rate: 0.6,
        sharpe_annualized: 1.0, max_drawdown: 0.05, profit_factor: 2,
      },
      short: {
        window: "30d", trades: 3, pnl_usdt: 4, pnl_pct: 0.5, win_rate: 0.5,
        sharpe_annualized: 0.5, max_drawdown: 0.03, profit_factor: 1.2,
      },
    };
    fetchMock.mockResolvedValueOnce(jsonResponse(sample));
    const result = await api.longShort();
    expect(lastUrl()).toBe(`${BASE}/bot-status/long-vs-short?days=30`);
    expect(result.long.trades).toBe(5);
    expect(result.short.trades).toBe(3);
  });

  test("respects custom days", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({
      long: { window: "7d", trades: 0, pnl_usdt: 0, pnl_pct: 0, win_rate: 0, sharpe_annualized: null, max_drawdown: 0, profit_factor: 0 },
      short: { window: "7d", trades: 0, pnl_usdt: 0, pnl_pct: 0, win_rate: 0, sharpe_annualized: null, max_drawdown: 0, profit_factor: 0 },
    }));
    await api.longShort(7);
    expect(lastUrl()).toBe(`${BASE}/bot-status/long-vs-short?days=7`);
  });
});

describe("api.equityCurve", () => {
  test("uses default days=30 and returns parsed EquityCurve", async () => {
    const sample: EquityCurve = {
      days: 30,
      points: [{ date: "2026-04-30T00:00:00Z", cumulative_pnl_usdt: 12.5 }],
    };
    fetchMock.mockResolvedValueOnce(jsonResponse(sample));
    const result = await api.equityCurve();
    expect(lastUrl()).toBe(`${BASE}/bot-status/equity-curve?days=30`);
    expect(result.points).toHaveLength(1);
    expect(result.points[0]?.cumulative_pnl_usdt).toBe(12.5);
  });

  test("respects custom days", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ days: 90, points: [] }));
    await api.equityCurve(90);
    expect(lastUrl()).toBe(`${BASE}/bot-status/equity-curve?days=90`);
  });
});

describe("api.assetUniverse", () => {
  test("calls correct URL and returns parsed AssetUniverse", async () => {
    const sample: AssetUniverse = {
      snapshot_at: "2026-05-01T00:00:00Z",
      entries: [{ symbol: "BTC/USDT", rank: 1, quote_volume_24h_usdt: 1_000_000_000 }],
    };
    fetchMock.mockResolvedValueOnce(jsonResponse(sample));
    const result = await api.assetUniverse();
    expect(lastUrl()).toBe(`${BASE}/bot-status/asset-universe`);
    expect(result.entries[0]?.rank).toBe(1);
  });
});

describe("existing api methods still work", () => {
  test("api.health is unchanged", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ status: "ok", version: "x" }));
    const result = await api.health();
    expect(lastUrl()).toBe(`${BASE}/health`);
    expect(result.status).toBe("ok");
  });
});
