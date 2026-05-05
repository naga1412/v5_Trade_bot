// SP-6 Phase D7: Tab 3 Scanner Radar fetch hook.
//
// Single REST endpoint (`/scanner/radar`) backed by a configurable polling
// interval (default 2 minutes per MASTER_PLAN §9). The hook exposes a manual
// `refetch()` so the toolbar's ⟳ button can force-refresh out of band, and an
// `error` field so the caller can render a banner when the radar endpoint
// fails. We intentionally do not deduplicate concurrent fetches: the polling
// cadence is conservative enough that overlap is unlikely in practice, and the
// toolbar's manual refresh always fires regardless.

import { useCallback, useEffect, useRef, useState } from "react";
import { api, type ScannerRadar, type ScannerRadarOptions } from "@/lib/api";

export interface UseScannerRadarOptions extends ScannerRadarOptions {
  /** Polling interval in milliseconds. Default: 120_000 (2 minutes). */
  refreshIntervalMs?: number;
}

export interface UseScannerRadarResult {
  data: ScannerRadar | null;
  error: Error | null;
  isLoading: boolean;
  refetch: () => Promise<void>;
}

const DEFAULT_INTERVAL_MS = 120_000;

export function useScannerRadar(
  opts: UseScannerRadarOptions = {},
): UseScannerRadarResult {
  const [data, setData] = useState<ScannerRadar | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [isLoading, setLoading] = useState<boolean>(true);

  // Latest opts mirror so refetch() (memoised once) always reads current values.
  const optsRef = useRef<UseScannerRadarOptions>(opts);
  optsRef.current = opts;

  const refetch = useCallback(async (): Promise<void> => {
    setLoading(true);
    try {
      // Build the args object incrementally so undefined keys aren't passed —
      // tsconfig has `exactOptionalPropertyTypes` enabled, so `{ market: undefined }`
      // is a type error against `ScannerRadarOptions`.
      const args: ScannerRadarOptions = {};
      const cur = optsRef.current;
      if (cur.market !== undefined) args.market = cur.market;
      if (cur.tf !== undefined) args.tf = cur.tf;
      if (cur.limit !== undefined) args.limit = cur.limit;
      const r = await api.scannerRadar(args);
      setData(r);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e : new Error(String(e)));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refetch();
    const ms = opts.refreshIntervalMs ?? DEFAULT_INTERVAL_MS;
    const id = window.setInterval(() => {
      void refetch();
    }, ms);
    return () => window.clearInterval(id);
  }, [opts.market, opts.tf, opts.limit, opts.refreshIntervalMs, refetch]);

  return { data, error, isLoading, refetch };
}
