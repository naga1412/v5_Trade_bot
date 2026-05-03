import { useCallback, useEffect, useState } from "react";
import { api, type PromotionGate } from "@/lib/api";

export interface UsePromotionGateResult {
  data: PromotionGate | null;
  error: string | null;
  loading: boolean;
  refetch: () => void;
}

export function usePromotionGate(): UsePromotionGateResult {
  const [data, setData] = useState<PromotionGate | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);

  const refetch = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api.promotionGate().then(
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
  }, [tick]);

  return { data, error, loading, refetch };
}
