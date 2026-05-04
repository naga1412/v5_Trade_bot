import { useCallback, useEffect, useState } from "react";
import { api, type PerAssetStat } from "@/lib/api";

export interface UsePerAssetStatsResult {
  data: PerAssetStat[] | null;
  error: string | null;
  loading: boolean;
  days: number;
  setDays: (d: number) => void;
  refetch: () => void;
}

export function usePerAssetStats(initialDays = 30): UsePerAssetStatsResult {
  const [days, setDays] = useState(initialDays);
  const [data, setData] = useState<PerAssetStat[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);

  const refetch = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api.perAssetStats(days).then(
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

  return { data, error, loading, days, setDays, refetch };
}
