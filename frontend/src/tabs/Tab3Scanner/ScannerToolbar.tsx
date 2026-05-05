// SP-6 Phase D2: Tab 3 Scanner Radar — toolbar row.
//
// Layout per MASTER_PLAN §9 lines 306-317 (left → right):
//   1. search assets text input (130px)
//   2. watchlist add text input (130px)
//   3. gold "★ Add" pill button
//   4. market dropdown ("Crypto 200+")
//   5. timeframe dropdown ("1h")
//   6. cyan asset count icon + asset count number input
//   7. refresh interval number input + "min" suffix
//   8. sort dropdown ("AI Score")
//   9. filter pills (FilterPills)
//   10. ⟳ Refresh button (right-aligned via ml-auto)
//
// v1 simplifications (per Phase D plan):
//   - Market dropdown shows only "Crypto" + a disabled "Stocks (soon)" hint —
//     SP-3 added stock/fx adapters but the scanner endpoint is crypto-only.
//   - Sort dropdown shows only "AI Score" — server-side sort defer to SP-6.5.
//   - Watchlist add + ★ Add button render but no-op for v1 (SP-7 adds favorites).

import { FilterPills, type ScannerFilter } from "./FilterPills";
import type { ScannerFilterCounts, ScannerRadar } from "@/lib/api";

export type Market = ScannerRadar["market"];
export type Timeframe = "1m" | "5m" | "15m" | "1h" | "4h" | "1d";
export type SortKey = "ai_score";

interface Props {
  market: Market;
  onMarketChange: (m: Market) => void;
  tf: Timeframe;
  onTfChange: (tf: Timeframe) => void;
  assetCount: number;
  onAssetCountChange: (n: number) => void;
  refreshMin: number;
  onRefreshMinChange: (n: number) => void;
  search: string;
  onSearchChange: (s: string) => void;
  watchlist: string;
  onWatchlistChange: (s: string) => void;
  onWatchlistAdd: () => void;
  sort: SortKey;
  onSortChange: (s: SortKey) => void;
  activeFilter: ScannerFilter;
  onFilterChange: (f: ScannerFilter) => void;
  filterCounts: ScannerFilterCounts | null;
  onRefresh: () => void;
}

const TFS: ReadonlyArray<Timeframe> = ["1m", "5m", "15m", "1h", "4h", "1d"];

export function ScannerToolbar(p: Props) {
  return (
    <div
      className="flex items-center gap-2 px-2 py-1 bg-bg-elevated border-b border-border overflow-x-auto whitespace-nowrap"
      role="toolbar"
      aria-label="scanner toolbar"
    >
      <input
        type="text"
        placeholder="Search assets…"
        value={p.search}
        onChange={(e) => p.onSearchChange(e.target.value)}
        aria-label="search assets"
        className="w-[130px] h-7 px-2 text-[10px] bg-bg-base border border-border rounded text-text-primary"
      />
      <input
        type="text"
        placeholder="Add to watchlist…"
        value={p.watchlist}
        onChange={(e) => p.onWatchlistChange(e.target.value)}
        aria-label="watchlist"
        className="w-[130px] h-7 px-2 text-[10px] bg-bg-base border border-border rounded text-text-primary"
      />
      <button
        type="button"
        onClick={p.onWatchlistAdd}
        className="h-7 px-2 text-[10px] uppercase tracking-wide bg-gold/15 text-gold border border-gold/40 rounded hover:bg-gold/30"
      >
        ★ Add
      </button>
      <select
        aria-label="market"
        value={p.market}
        onChange={(e) => p.onMarketChange(e.target.value as Market)}
        className="h-7 px-2 text-[10px] bg-bg-base border border-border rounded text-text-primary"
      >
        <option value="crypto">Crypto 200+</option>
      </select>
      <select
        aria-label="timeframe"
        value={p.tf}
        onChange={(e) => p.onTfChange(e.target.value as Timeframe)}
        className="h-7 px-2 text-[10px] bg-bg-base border border-border rounded text-text-primary"
      >
        {TFS.map((tf) => (
          <option key={tf} value={tf}>{tf}</option>
        ))}
      </select>
      <span className="text-cyan text-[10px]" aria-hidden="true">⊕</span>
      <input
        type="number"
        min={1}
        max={500}
        aria-label="asset count"
        value={p.assetCount}
        onChange={(e) => p.onAssetCountChange(Number(e.target.value))}
        className="w-[60px] h-7 px-1 text-[10px] bg-bg-base border border-border rounded text-text-primary text-right"
      />
      <input
        type="number"
        min={1}
        max={60}
        aria-label="refresh minutes"
        value={p.refreshMin}
        onChange={(e) => p.onRefreshMinChange(Number(e.target.value))}
        className="w-[44px] h-7 px-1 text-[10px] bg-bg-base border border-border rounded text-text-primary text-right"
      />
      <span className="text-text-tertiary text-[8px] uppercase">min</span>
      <select
        aria-label="sort"
        value={p.sort}
        onChange={(e) => p.onSortChange(e.target.value as SortKey)}
        className="h-7 px-2 text-[10px] bg-bg-base border border-border rounded text-text-primary"
      >
        <option value="ai_score">AI Score</option>
      </select>
      <FilterPills
        active={p.activeFilter}
        counts={p.filterCounts}
        onChange={p.onFilterChange}
      />
      <button
        type="button"
        onClick={p.onRefresh}
        aria-label="refresh"
        className="ml-auto h-7 px-2 text-[10px] bg-bg-base border border-border rounded hover:bg-bg-panel text-text-primary"
      >
        ⟳ Refresh
      </button>
    </div>
  );
}
