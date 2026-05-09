import { useEffect, useState, useRef } from "react";
import { api, type LivePrediction } from "@/lib/api";
import { TradingRadarSocket } from "@/lib/ws";

export function useLivePrediction(symbol: string, timeframe: string, signalId?: string) {
  const [data, setData] = useState<LivePrediction | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const sockRef = useRef<TradingRadarSocket | null>(null);

  useEffect(() => {
    let cancelled = false;
    const symbolPath = symbol.replace("/", "-");

    api.predict(symbolPath, timeframe, signalId).then(
      (d) => { if (!cancelled) setData(d); },
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
      const m = msg as { channel?: string; payload?: LivePrediction };
      if (m.channel === "live_prediction" && m.payload) {
        setData(m.payload);
      }
    });

    return () => {
      cancelled = true;
      off();
      sock.close();
    };
  }, [symbol, timeframe, signalId]);

  return { data, err };
}
