import { useCallback, useEffect, useState } from "react";
import { api, type EquityCurve } from "@/lib/api";

export interface UseEquityCurveResult {
  data: EquityCurve | null;
  error: string | null;
  loading: boolean;
  refetch: () => void;
}

export function useEquityCurve(days = 30): UseEquityCurveResult {
  const [data, setData] = useState<EquityCurve | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);

  const refetch = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api.equityCurve(days).then(
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
