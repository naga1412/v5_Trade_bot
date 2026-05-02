import { useEffect, useState } from "react";
import { fetchJson } from "@/lib/api";

export interface ChartCandle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
}

export function useChartHistory(symbol: string, timeframe: string, limit = 500) {
  const [candles, setCandles] = useState<ChartCandle[]>([]);
  useEffect(() => {
    let cancelled = false;
    const path = `/candles/${symbol.replace("/", "-")}/${timeframe}?limit=${limit}`;
    fetchJson<ChartCandle[]>(path).then((cs) => {
      if (!cancelled) setCandles(cs);
    });
    return () => {
      cancelled = true;
    };
  }, [symbol, timeframe, limit]);
  return candles;
}
