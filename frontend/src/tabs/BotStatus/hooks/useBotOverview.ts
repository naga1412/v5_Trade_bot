import { useCallback, useEffect, useState } from "react";
import { api, type BotOverview } from "@/lib/api";
import { useShadowUpdates } from "@/hooks/useShadowUpdates";

export interface UseBotOverviewResult {
  data: BotOverview | null;
  error: string | null;
  loading: boolean;
  refetch: () => void;
}

export function useBotOverview(): UseBotOverviewResult {
  const [data, setData] = useState<BotOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const { lastClosed } = useShadowUpdates();

  const refetch = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api.botOverview().then(
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
  }, [tick, lastClosed]);

  return { data, error, loading, refetch };
}
