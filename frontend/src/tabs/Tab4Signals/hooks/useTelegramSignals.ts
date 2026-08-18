// Phase 4: auto-refresh fetch hook for the Signals tab. Mirrors
// useScannerRadar's shape exactly -- same polling + manual-refetch
// pattern, per the operator's explicit "match the scanner's ~2min
// precedent" instruction.

import { useCallback, useEffect, useRef, useState } from "react";
import { api, type TelegramSignal, type TelegramSignalsFilters } from "@/lib/api";

export interface UseTelegramSignalsOptions extends TelegramSignalsFilters {
  /** Polling interval in milliseconds. Default: 120_000 (2 minutes). */
  refreshIntervalMs?: number;
}

export interface UseTelegramSignalsResult {
  data: TelegramSignal[] | null;
  error: Error | null;
  isLoading: boolean;
  refetch: () => Promise<void>;
}

const DEFAULT_INTERVAL_MS = 120_000;

export function useTelegramSignals(
  opts: UseTelegramSignalsOptions = {},
): UseTelegramSignalsResult {
  const [data, setData] = useState<TelegramSignal[] | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [isLoading, setLoading] = useState<boolean>(true);

  // Latest opts mirror so refetch() (memoised once) always reads current values.
  const optsRef = useRef<UseTelegramSignalsOptions>(opts);
  optsRef.current = opts;

  const refetch = useCallback(async (): Promise<void> => {
    setLoading(true);
    try {
      // Build the args object incrementally so undefined keys aren't passed —
      // tsconfig has `exactOptionalPropertyTypes` enabled, so `{ direction: undefined }`
      // is a type error against `TelegramSignalsFilters`.
      const args: TelegramSignalsFilters = {};
      const cur = optsRef.current;
      if (cur.limit !== undefined) args.limit = cur.limit;
      if (cur.direction !== undefined) args.direction = cur.direction;
      if (cur.symbol_source !== undefined) args.symbol_source = cur.symbol_source;
      const r = await api.telegramSignals(args);
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
  }, [opts.limit, opts.direction, opts.symbol_source, opts.refreshIntervalMs, refetch]);

  return { data, error, isLoading, refetch };
}
