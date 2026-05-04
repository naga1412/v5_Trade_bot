import { useEffect, useState } from "react";
import { TradingRadarSocket } from "@/lib/ws";

// --- Event payload types (match backend app/ws/shadow_updates.py wire format) ---

export interface ShadowPositionOpenedEvent {
  type: "shadow_position_opened";
  symbol: string;
  direction: "LONG" | "SHORT";
  entry_price: number;
  sl: number;
  tp: number;
  signal_id: string;
  score: number;
  confidence: number;
  opened_at: string;
}

export interface ShadowPositionClosedEvent {
  type: "shadow_position_closed";
  symbol: string;
  direction: "LONG" | "SHORT";
  entry_price: number;
  exit_price: number;
  exit_reason: "TAKE_PROFIT" | "STOP_LOSS" | "TIMEOUT";
  pnl_pct: number;
  pnl_usdt: number;
  bars_held: number;
  signal_id: string;
  closed_at: string;
}

export interface ShadowPnlTickEvent {
  type: "shadow_pnl_tick";
  symbol: string;
  current_price: number;
  unrealized_pnl_pct: number;
}

export interface UniverseRefreshedEvent {
  type: "universe_refreshed";
  snapshot_at: string;
  top_30: string[];
  removed_from_top_30: string[];
}

export type ShadowUpdateEvent =
  | ShadowPositionOpenedEvent
  | ShadowPositionClosedEvent
  | ShadowPnlTickEvent
  | UniverseRefreshedEvent;

export interface ShadowUpdatesState {
  lastOpened: ShadowPositionOpenedEvent | null;
  lastClosed: ShadowPositionClosedEvent | null;
  lastPnlTick: Record<string, ShadowPnlTickEvent>;
  lastUniverseRefresh: UniverseRefreshedEvent | null;
}

export interface SocketLike {
  connect(): void;
  subscribe(channel: string, params: Record<string, unknown>): void;
  on(listener: (msg: unknown) => void): () => void;
  close(): void;
}

export interface UseShadowUpdatesOptions {
  socketFactory?: () => SocketLike;
  clientId?: string;
}

const CHANNEL = "shadow_updates";

const INITIAL_STATE: ShadowUpdatesState = {
  lastOpened: null,
  lastClosed: null,
  lastPnlTick: {},
  lastUniverseRefresh: null,
};

function isShadowUpdate(
  msg: unknown,
): msg is { channel: string; key?: unknown; payload: ShadowUpdateEvent } {
  if (typeof msg !== "object" || msg === null) return false;
  const m = msg as { channel?: unknown; payload?: unknown };
  if (m.channel !== CHANNEL) return false;
  if (typeof m.payload !== "object" || m.payload === null) return false;
  const p = m.payload as { type?: unknown };
  return (
    p.type === "shadow_position_opened" ||
    p.type === "shadow_position_closed" ||
    p.type === "shadow_pnl_tick" ||
    p.type === "universe_refreshed"
  );
}

export function useShadowUpdates(
  opts?: UseShadowUpdatesOptions,
): ShadowUpdatesState {
  const [state, setState] = useState<ShadowUpdatesState>(INITIAL_STATE);

  // Stash factory + clientId in refs so the effect can read the latest values
  // without re-running. We deliberately want a single socket per hook lifetime.
  const factory = opts?.socketFactory;
  const clientId = opts?.clientId;

  useEffect(() => {
    const sock: SocketLike = factory
      ? factory()
      : new TradingRadarSocket(clientId ?? crypto.randomUUID());

    sock.connect();
    sock.subscribe(CHANNEL, {});

    const off = sock.on((msg: unknown) => {
      if (!isShadowUpdate(msg)) return;
      const evt = msg.payload;
      setState((prev) => {
        switch (evt.type) {
          case "shadow_position_opened":
            return { ...prev, lastOpened: evt };
          case "shadow_position_closed":
            return { ...prev, lastClosed: evt };
          case "shadow_pnl_tick":
            return {
              ...prev,
              lastPnlTick: { ...prev.lastPnlTick, [evt.symbol]: evt },
            };
          case "universe_refreshed":
            return { ...prev, lastUniverseRefresh: evt };
        }
      });
    });

    return () => {
      off();
      sock.close();
    };
    // We intentionally bind to the factory / clientId provided at mount only —
    // this hook owns a single socket for its lifetime.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return state;
}
