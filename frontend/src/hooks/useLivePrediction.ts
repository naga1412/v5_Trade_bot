import { useEffect, useState, useRef } from "react";
import { api, type LivePrediction } from "@/lib/api";
import { TradingRadarSocket } from "@/lib/ws";

// Minimal shape guard: a payload is usable only if it has the two
// nested objects every panel below requires. WebSocket frames during
// backend restart / partial publish can arrive with `final` or
// `trade_setup` missing → panels deref → render throws → ErrorBoundary
// kills the whole tab. Reject those at the source instead.
function isUsablePrediction(x: unknown): x is LivePrediction {
  if (x === null || typeof x !== "object") return false;
  const p = x as Partial<LivePrediction>;
  return (
    p.final !== undefined &&
    p.final !== null &&
    typeof p.final === "object" &&
    p.trade_setup !== undefined &&
    p.trade_setup !== null &&
    typeof p.trade_setup === "object"
  );
}

export function useLivePrediction(symbol: string, timeframe: string, signalId?: string) {
  const [data, setData] = useState<LivePrediction | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const sockRef = useRef<TradingRadarSocket | null>(null);

  useEffect(() => {
    let cancelled = false;
    const symbolPath = symbol.replace("/", "-");

    setErr(null);
    api.predict(symbolPath, timeframe, signalId).then(
      (d) => {
        if (cancelled) return;
        if (isUsablePrediction(d)) {
          setData(d);
        } else {
          setErr(`malformed prediction payload for ${symbol}`);
        }
      },
      (e: Error) => { if (!cancelled) setErr(e.message); },
    );

    // WS path must use the URL-safe symbolPath (BTC-USDT), not raw symbol
    // (BTC/USDT). The backend route /ws/v1/{client_id} treats the slash as
    // a path separator, which made the connection 404/403 on every request
    // and caused the dashboard to lose all real-time updates.
    const sock = new TradingRadarSocket(`tab1-${symbolPath}-${timeframe}`);
    sockRef.current = sock;
    sock.connect();
    sock.subscribe("live_prediction", { symbol, timeframe });

    const off = sock.on((msg: unknown) => {
      const m = msg as { channel?: string; payload?: unknown };
      if (m.channel !== "live_prediction" || m.payload == null) return;
      // Drop frames that don't have the required nested shapes — keeping
      // the previous valid `data` is far better than tripping a panel
      // crash that blanks the whole tab via ErrorBoundary.
      if (!isUsablePrediction(m.payload)) {
        // eslint-disable-next-line no-console
        console.warn("[useLivePrediction] dropped malformed WS payload", m.payload);
        return;
      }
      setData(m.payload);
    });

    return () => {
      cancelled = true;
      off();
      sock.close();
    };
  }, [symbol, timeframe, signalId]);

  return { data, err };
}
