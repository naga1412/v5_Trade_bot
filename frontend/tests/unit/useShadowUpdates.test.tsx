import { act, renderHook } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import { useShadowUpdates } from "@/hooks/useShadowUpdates";

interface FakeSocket {
  connect: ReturnType<typeof vi.fn>;
  subscribe: ReturnType<typeof vi.fn>;
  on: (l: (m: unknown) => void) => () => void;
  close: ReturnType<typeof vi.fn>;
  emit: (m: unknown) => void;
  listenerCount: () => number;
}

function makeFakeSocket(): FakeSocket {
  const listeners = new Set<(m: unknown) => void>();
  return {
    connect: vi.fn(),
    subscribe: vi.fn(),
    on: (l: (m: unknown) => void) => {
      listeners.add(l);
      return () => {
        listeners.delete(l);
      };
    },
    close: vi.fn(),
    emit: (m: unknown) => {
      for (const l of listeners) l(m);
    },
    listenerCount: () => listeners.size,
  };
}

describe("useShadowUpdates", () => {
  test("initial state is all-null with empty pnl tick map", () => {
    const sock = makeFakeSocket();
    const { result } = renderHook(() =>
      useShadowUpdates({ socketFactory: () => sock }),
    );
    expect(result.current.lastOpened).toBeNull();
    expect(result.current.lastClosed).toBeNull();
    expect(result.current.lastUniverseRefresh).toBeNull();
    expect(result.current.lastPnlTick).toEqual({});
  });

  test("connects + subscribes to shadow_updates with empty params on mount", () => {
    const sock = makeFakeSocket();
    renderHook(() => useShadowUpdates({ socketFactory: () => sock }));
    expect(sock.connect).toHaveBeenCalledTimes(1);
    expect(sock.subscribe).toHaveBeenCalledTimes(1);
    expect(sock.subscribe).toHaveBeenCalledWith("shadow_updates", {});
  });

  test("routes shadow_position_opened into lastOpened", () => {
    const sock = makeFakeSocket();
    const { result } = renderHook(() =>
      useShadowUpdates({ socketFactory: () => sock }),
    );
    act(() => {
      sock.emit({
        channel: "shadow_updates",
        key: { symbol: "BTC/USDT" },
        payload: {
          type: "shadow_position_opened",
          symbol: "BTC/USDT",
          direction: "LONG",
          entry_price: 100,
          sl: 95,
          tp: 110,
          signal_id: "sig-1",
          score: 0.8,
          confidence: 0.7,
          opened_at: "2026-05-01T12:00:00Z",
        },
      });
    });
    expect(result.current.lastOpened?.symbol).toBe("BTC/USDT");
    expect(result.current.lastOpened?.direction).toBe("LONG");
    expect(result.current.lastOpened?.signal_id).toBe("sig-1");
  });

  test("routes shadow_position_closed into lastClosed", () => {
    const sock = makeFakeSocket();
    const { result } = renderHook(() =>
      useShadowUpdates({ socketFactory: () => sock }),
    );
    act(() => {
      sock.emit({
        channel: "shadow_updates",
        key: { symbol: "ETH/USDT" },
        payload: {
          type: "shadow_position_closed",
          symbol: "ETH/USDT",
          direction: "SHORT",
          entry_price: 200,
          exit_price: 190,
          exit_reason: "TAKE_PROFIT",
          pnl_pct: 5,
          pnl_usdt: 50,
          bars_held: 10,
          signal_id: "sig-2",
          closed_at: "2026-05-01T13:00:00Z",
        },
      });
    });
    expect(result.current.lastClosed?.symbol).toBe("ETH/USDT");
    expect(result.current.lastClosed?.exit_reason).toBe("TAKE_PROFIT");
    expect(result.current.lastClosed?.pnl_usdt).toBe(50);
  });

  test("routes shadow_pnl_tick into per-symbol map and aggregates multiple symbols", () => {
    const sock = makeFakeSocket();
    const { result } = renderHook(() =>
      useShadowUpdates({ socketFactory: () => sock }),
    );
    act(() => {
      sock.emit({
        channel: "shadow_updates",
        key: { symbol: "BTC/USDT" },
        payload: {
          type: "shadow_pnl_tick",
          symbol: "BTC/USDT",
          current_price: 105,
          unrealized_pnl_pct: 5,
        },
      });
    });
    act(() => {
      sock.emit({
        channel: "shadow_updates",
        key: { symbol: "ETH/USDT" },
        payload: {
          type: "shadow_pnl_tick",
          symbol: "ETH/USDT",
          current_price: 195,
          unrealized_pnl_pct: -2.5,
        },
      });
    });
    expect(result.current.lastPnlTick["BTC/USDT"]?.current_price).toBe(105);
    expect(result.current.lastPnlTick["ETH/USDT"]?.unrealized_pnl_pct).toBe(-2.5);
    expect(Object.keys(result.current.lastPnlTick)).toHaveLength(2);
  });

  test("subsequent pnl tick for same symbol overwrites previous", () => {
    const sock = makeFakeSocket();
    const { result } = renderHook(() =>
      useShadowUpdates({ socketFactory: () => sock }),
    );
    act(() => {
      sock.emit({
        channel: "shadow_updates",
        key: { symbol: "BTC/USDT" },
        payload: {
          type: "shadow_pnl_tick",
          symbol: "BTC/USDT",
          current_price: 100,
          unrealized_pnl_pct: 0,
        },
      });
    });
    act(() => {
      sock.emit({
        channel: "shadow_updates",
        key: { symbol: "BTC/USDT" },
        payload: {
          type: "shadow_pnl_tick",
          symbol: "BTC/USDT",
          current_price: 110,
          unrealized_pnl_pct: 10,
        },
      });
    });
    expect(result.current.lastPnlTick["BTC/USDT"]?.current_price).toBe(110);
    expect(Object.keys(result.current.lastPnlTick)).toHaveLength(1);
  });

  test("routes universe_refreshed into lastUniverseRefresh", () => {
    const sock = makeFakeSocket();
    const { result } = renderHook(() =>
      useShadowUpdates({ socketFactory: () => sock }),
    );
    act(() => {
      sock.emit({
        channel: "shadow_updates",
        key: {},
        payload: {
          type: "universe_refreshed",
          snapshot_at: "2026-05-01T00:00:00Z",
          top_30: ["BTC/USDT", "ETH/USDT"],
          removed_from_top_30: ["DOGE/USDT"],
        },
      });
    });
    expect(result.current.lastUniverseRefresh?.top_30).toEqual(["BTC/USDT", "ETH/USDT"]);
    expect(result.current.lastUniverseRefresh?.removed_from_top_30).toEqual(["DOGE/USDT"]);
  });

  test("ignores messages from a different channel", () => {
    const sock = makeFakeSocket();
    const { result } = renderHook(() =>
      useShadowUpdates({ socketFactory: () => sock }),
    );
    act(() => {
      sock.emit({
        channel: "live_prediction",
        key: { symbol: "BTC/USDT" },
        payload: {
          type: "shadow_position_opened",
          symbol: "BTC/USDT",
          direction: "LONG",
          entry_price: 100,
          sl: 95,
          tp: 110,
          signal_id: "sig-x",
          score: 0,
          confidence: 0,
          opened_at: "2026-05-01T12:00:00Z",
        },
      });
    });
    expect(result.current.lastOpened).toBeNull();
    expect(result.current.lastPnlTick).toEqual({});
  });

  test("calls socket.close on unmount", () => {
    const sock = makeFakeSocket();
    const { unmount } = renderHook(() =>
      useShadowUpdates({ socketFactory: () => sock }),
    );
    expect(sock.close).not.toHaveBeenCalled();
    unmount();
    expect(sock.close).toHaveBeenCalledTimes(1);
  });
});
