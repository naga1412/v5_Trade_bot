import { useCallback, useEffect, useState } from "react";
import { api, type RecentTrade, type RecentTradesFilters } from "@/lib/api";

export interface UseRecentTradesResult {
  data: RecentTrade[] | null;
  error: string | null;
  loading: boolean;
  filters: RecentTradesFilters;
  setFilters: (f: RecentTradesFilters) => void;
  refetch: () => void;
}

export function useRecentTrades(initial: RecentTradesFilters = { limit: 100 }): UseRecentTradesResult {
  const [filters, setFilters] = useState<RecentTradesFilters>(initial);
  const [data, setData] = useState<RecentTrade[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);

  const refetch = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api.recentTrades(filters).then(
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
  }, [tick, filters]);

  return { data, error, loading, filters, setFilters, refetch };
}
