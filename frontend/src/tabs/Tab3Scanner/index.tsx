// SP-6 Phase D1: Tab 3 Scanner Radar — top-level page wrapper.
//
// Owns the toolbar's local state (market, tf, asset count, refresh interval,
// search query, watchlist add box, sort, active filter pill) and threads it
// through the data hook + child components. Layout per MASTER_PLAN §9:
//
//   ScannerToolbar      (sticky 1-row strip, scrolls horizontally on mobile)
//   HybridSupervisorBar (cyan progress 0..100%)
//   <error banner>      (only when useScannerRadar surfaces an Error)
//   2-column grid       (BullishColumn | BearishColumn — stacks ≤md)
//   <footer>            (scanned count • tf  ───  refresh cadence • hint)
//
// Polling is owned by useScannerRadar (configurable; defaults to 2 min). The
// toolbar's ⟳ Refresh button calls `refetch` directly so power users don't
// have to wait for the interval.

import { useState } from "react";
import { ScannerToolbar, type Market, type Timeframe, type SortKey } from "./ScannerToolbar";
import { HybridSupervisorBar } from "./HybridSupervisorBar";
import { BullishColumn } from "./BullishColumn";
import { BearishColumn } from "./BearishColumn";
import { FastScanRadar } from "./FastScanRadar";
import { useScannerRadar } from "./hooks/useScannerRadar";
import type { ScannerFilter } from "./FilterPills";

const DEFAULT_LIMIT = 50; // per direction; lower than spec's 200 for Lighthouse
const DEFAULT_REFRESH_MIN = 2;

export function Tab3Scanner() {
  const [market, setMarket] = useState<Market>("crypto");
  const [tf, setTf] = useState<Timeframe>("1h");
  const [assetCount, setAssetCount] = useState<number>(DEFAULT_LIMIT);
  const [refreshMin, setRefreshMin] = useState<number>(DEFAULT_REFRESH_MIN);
  const [search, setSearch] = useState<string>("");
  const [watchlist, setWatchlist] = useState<string>("");
  const [sort, setSort] = useState<SortKey>("ai_score");
  const [filter, setFilter] = useState<ScannerFilter>("all");

  const { data, error, refetch } = useScannerRadar({
    market,
    tf,
    limit: assetCount,
    refreshIntervalMs: Math.max(1, refreshMin) * 60_000,
  });

  function onWatchlistAdd(): void {
    // v1: no-op. SP-7 will wire a per-user favorites table; keeping the input
    // controlled so the click feels responsive in the meantime.
    setWatchlist("");
  }

  return (
    <div className="h-full flex flex-col bg-bg-base">
      <ScannerToolbar
        market={market}
        onMarketChange={setMarket}
        tf={tf}
        onTfChange={setTf}
        assetCount={assetCount}
        onAssetCountChange={setAssetCount}
        refreshMin={refreshMin}
        onRefreshMinChange={setRefreshMin}
        search={search}
        onSearchChange={setSearch}
        watchlist={watchlist}
        onWatchlistChange={setWatchlist}
        onWatchlistAdd={onWatchlistAdd}
        sort={sort}
        onSortChange={setSort}
        activeFilter={filter}
        onFilterChange={setFilter}
        filterCounts={data?.filter_counts ?? null}
        onRefresh={() => { void refetch(); }}
      />
      <HybridSupervisorBar progress={data?.supervisor_progress ?? null} />
      {error && (
        <div role="alert" className="px-3 py-1 text-red text-[10px] border-b border-red/40 bg-red/15">
          {error.message}
        </div>
      )}
      <div className="flex-1 min-h-0 overflow-y-auto p-2">
        {/* Feature 4 — fast indicator-only scan over the asset universe.
            Sits above the legacy ML-driven radar; the two are
            complementary (breadth scan vs the per-symbol ML view). */}
        <FastScanRadar timeframe={tf} />
      <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
        <BullishColumn
          cards={data?.bullish ?? []}
          search={search}
          filter={filter}
          tf={tf}
        />
        <BearishColumn
          cards={data?.bearish ?? []}
          search={search}
          filter={filter}
          tf={tf}
        />
      </div>
      </div>
      <footer className="text-[8px] text-text-tertiary border-t border-border px-2 py-1 flex justify-between">
        <span>
          Scanning {data?.scanned_count ?? 0} {market} • {tf}
        </span>
        <span>
          Auto-refresh every {refreshMin} min • Click card to view chart
        </span>
      </footer>
    </div>
  );
}
