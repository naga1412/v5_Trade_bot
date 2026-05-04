import { useCallback, useEffect, useState } from "react";
import { api, type LongShortBreakdown } from "@/lib/api";

export interface UseLongShortBreakdownResult {
  data: LongShortBreakdown | null;
  error: string | null;
  loading: boolean;
  refetch: () => void;
}

export function useLongShortBreakdown(days = 30): UseLongShortBreakdownResult {
  const [data, setData] = useState<LongShortBreakdown | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);

  const refetch = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api.longShort(days).then(
      (d) => {
        if (cancelled) return;
        setData(d);
        setError(null);
        setLoading(false);
      },
      (e: Error) => {
        if (cancelled) return;
        setError(e.message);
        setLoading(false);
      },
    );
    return () => { cancelled = true; };
  }, [tick, days]);

  return { data, error, loading, refetch };
}
