// Phase 4: Signals tab -- the operator's primary workflow surface for
// manually trading these signals. Read-only, sourced from
// telegram_signals via useTelegramSignals, never recomputed.
// Auto-refreshes every ~2min (matches the scanner's precedent) plus a
// manual refresh control.
//
// Toolbar-plus-hook shape follows Tab3Scanner/index.tsx: local state
// for each filter, threaded into the data hook's options, plus a
// manual ⟳ Refresh button that calls refetch() directly.

import { useState } from "react";
import { useTelegramSignals, type UseTelegramSignalsOptions } from "./hooks/useTelegramSignals";
import { SignalRow } from "./SignalRow";

type DirectionFilter = "all" | "LONG" | "SHORT";
type CohortFilter = "all" | "established_top20" | "liquidity_added_spot" | "futures_poll";

const DEFAULT_REFRESH_MIN = 2;

export function Tab4Signals() {
  const [directionFilter, setDirectionFilter] = useState<DirectionFilter>("all");
  const [cohortFilter, setCohortFilter] = useState<CohortFilter>("all");

  // Built incrementally (not `direction: directionFilter === "all" ? undefined : ...`)
  // because tsconfig has `exactOptionalPropertyTypes` enabled -- the hook's own
  // TelegramSignalsFilters declares `direction?:`/`symbol_source?:` without `| undefined`,
  // so an explicit `undefined` value is a type error even though the key is optional.
  // Same workaround useTelegramSignals.ts itself already applies internally.
  const hookOptions: UseTelegramSignalsOptions = {
    limit: 100,
    refreshIntervalMs: DEFAULT_REFRESH_MIN * 60_000,
  };
  if (directionFilter !== "all") hookOptions.direction = directionFilter;
  if (cohortFilter !== "all") hookOptions.symbol_source = cohortFilter;

  const { data, error, isLoading, refetch } = useTelegramSignals(hookOptions);

  return (
    <div className="h-full flex flex-col bg-bg-base">
      <div className="flex items-center gap-2 p-2 border-b border-border bg-bg-elevated">
        <select
          value={directionFilter}
          onChange={(e) => setDirectionFilter(e.target.value as DirectionFilter)}
          className="text-xs bg-bg-base border border-border rounded px-2 py-1"
        >
          <option value="all">All directions</option>
          <option value="LONG">LONG</option>
          <option value="SHORT">SHORT</option>
        </select>
        <select
          value={cohortFilter}
          onChange={(e) => setCohortFilter(e.target.value as CohortFilter)}
          className="text-xs bg-bg-base border border-border rounded px-2 py-1"
        >
          <option value="all">All cohorts</option>
          <option value="established_top20">Established only</option>
          <option value="liquidity_added_spot">New — spot only</option>
          <option value="futures_poll">New — futures only</option>
        </select>
        <button
          onClick={() => { void refetch(); }}
          className="text-xs bg-bg-base border border-border rounded px-2 py-1 ml-auto"
        >
          ⟳ Refresh
        </button>
        {isLoading && <span className="text-[10px] text-text-tertiary">loading…</span>}
      </div>

      {error && (
        <div className="p-2 text-red text-xs">Failed to load signals: {error.message}</div>
      )}

      <div className="flex-1 overflow-auto">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-bg-base">
            <tr className="text-left text-text-tertiary uppercase text-[10px]">
              <th className="px-2 py-1">Sent</th>
              <th className="px-2 py-1">Symbol</th>
              <th className="px-2 py-1">Dir</th>
              <th className="px-2 py-1">Entry</th>
              <th className="px-2 py-1">SL</th>
              <th className="px-2 py-1">TP</th>
              <th className="px-2 py-1">RR</th>
              <th className="px-2 py-1">Conf</th>
              <th className="px-2 py-1">Status</th>
              <th className="px-2 py-1">Cohort</th>
              <th className="px-2 py-1">Liquidity</th>
            </tr>
          </thead>
          <tbody>
            {(data ?? []).map((s) => (
              <SignalRow key={s.signal_id} signal={s} />
            ))}
          </tbody>
        </table>
        {data && data.length === 0 && (
          <div className="p-4 text-center text-text-tertiary text-xs">No signals yet</div>
        )}
      </div>
    </div>
  );
}
