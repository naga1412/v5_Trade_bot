import { useCallback, useEffect, useState } from "react";
import { api, type OpenPosition } from "@/lib/api";
import { useShadowUpdates } from "@/hooks/useShadowUpdates";

export interface UseOpenPositionsLiveResult {
  data: OpenPosition[] | null;
  error: string | null;
  loading: boolean;
  refetch: () => void;
}

export function useOpenPositionsLive(): UseOpenPositionsLiveResult {
  const [data, setData] = useState<OpenPosition[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const { lastOpened, lastClosed, lastPnlTick } = useShadowUpdates();

  const refetch = useCallback(() => setTick((t) => t + 1), []);

  // Initial fetch + refetch when an open/close event arrives.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api.openPositions().then(
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
  }, [tick, lastOpened, lastClosed]);

  // Apply live PnL ticks in-place without refetching.
  useEffect(() => {
    if (data == null || data.length === 0) return;
    setData((prev) => {
      if (prev == null) return prev;
      let mutated = false;
      const next = prev.map((p) => {
        const t = lastPnlTick[p.symbol];
        if (!t) return p;
        if (
          p.current_price === t.current_price &&
          p.unrealized_pnl_pct === t.unrealized_pnl_pct
        ) {
          return p;
        }
        mutated = true;
        return {
          ...p,
          current_price: t.current_price,
          unrealized_pnl_pct: t.unrealized_pnl_pct,
        };
      });
      return mutated ? next : prev;
    });
    // We intentionally do not depend on `data` here — that would loop. We only
    // re-run when lastPnlTick changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastPnlTick]);

  return { data, error, loading, refetch };
}
