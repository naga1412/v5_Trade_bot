import { useMemo, useState } from "react";
import { Panel } from "@/components/ui/Panel";
import { usePerAssetStats } from "./hooks/usePerAssetStats";
import type { PerAssetStat } from "@/lib/api";

type SortKey = "symbol" | "trades" | "win_rate" | "avg_rr" | "pnl_usdt" | "sharpe_annualized";
type SortDir = "asc" | "desc";

const COLUMNS: { key: SortKey; label: string }[] = [
  { key: "symbol", label: "Symbol" },
  { key: "trades", label: "Trades" },
  { key: "win_rate", label: "Win%" },
  { key: "avg_rr", label: "RR" },
  { key: "pnl_usdt", label: "P&L" },
  { key: "sharpe_annualized", label: "Sharpe" },
];

// PR10.6 T-UI.3 completion: "Last trade" is rendered per-row but not sortable
// (timestamps have no useful comparison in this view — winners/losers ranking
// matters more than recency).
const LAST_TRADE_LABEL = "Last trade";

function fmtPnl(v: number): string {
  const sign = v >= 0 ? "+" : "-";
  return `${sign}$${Math.abs(v).toFixed(2)}`;
}

function fmtPct(v: number): string {
  return `${Math.round(v * 100)}%`;
}

function fmtNum(v: number | null, dp = 2): string {
  if (v == null) return "—";
  return v.toFixed(dp);
}

// PR10.6 T-UI.3 completion: per-row "last trade" timestamp as relative time.
// Returns "—" for null, "Xs ago" / "Xm ago" / "Xh ago" / "Xd ago" otherwise.
// Tested via PerAssetTable.test.tsx; intentionally inline (no new util file).
function fmtRelativeTime(iso: string | null, nowMs?: number): string {
  if (iso == null) return "—";
  const t = new Date(iso).getTime();
  if (!Number.isFinite(t)) return "—";
  const diffMs = (nowMs ?? Date.now()) - t;
  if (diffMs < 0) return "just now";
  const sec = Math.floor(diffMs / 1000);
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  return `${day}d ago`;
}

function pnlClass(v: number): string {
  return v >= 0 ? "text-green" : "text-red";
}

function compare(a: PerAssetStat, b: PerAssetStat, key: SortKey, dir: SortDir): number {
  const av = a[key];
  const bv = b[key];
  let cmp: number;
  if (typeof av === "string" && typeof bv === "string") {
    cmp = av.localeCompare(bv);
  } else {
    const an = (av as number | null) ?? -Infinity;
    const bn = (bv as number | null) ?? -Infinity;
    cmp = an - bn;
  }
  return dir === "asc" ? cmp : -cmp;
}

function RefreshButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label="refresh"
      className="text-[8px] text-text-tertiary hover:text-text-secondary px-1"
    >
      ⟳
    </button>
  );
}

export function PerAssetTable() {
  const { data, error, refetch } = usePerAssetStats();
  const [sortKey, setSortKey] = useState<SortKey>("pnl_usdt");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  const sorted = useMemo(() => {
    if (!data) return null;
    const copy = [...data];
    copy.sort((a, b) => compare(a, b, sortKey, sortDir));
    return copy;
  }, [data, sortKey, sortDir]);

  function clickHeader(key: SortKey) {
    if (key === sortKey) {
      setSortDir(sortDir === "desc" ? "asc" : "desc");
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
  }

  if (error) {
    return (
      <Panel title="Per-Asset Stats" intensity="alert" rightSlot={<RefreshButton onClick={refetch} />}>
        <span className="text-red">Error: {error}</span>
      </Panel>
    );
  }

  if (!sorted) {
    return (
      <Panel title="Per-Asset Stats" rightSlot={<RefreshButton onClick={refetch} />}>
        <span>—</span>
      </Panel>
    );
  }

  return (
    <Panel title="Per-Asset Stats" rightSlot={<RefreshButton onClick={refetch} />}>
      <table role="table" className="w-full text-left">
        <thead>
          <tr role="row">
            {COLUMNS.map((col) => (
              <th
                key={col.key}
                role="columnheader"
                scope="col"
                className="text-text-tertiary uppercase text-[7.5px] cursor-pointer select-none px-1 py-0.5"
                onClick={() => clickHeader(col.key)}
              >
                {col.label}
                {sortKey === col.key ? (sortDir === "desc" ? " ↓" : " ↑") : ""}
              </th>
            ))}
            {/* PR10.6 T-UI.3 completion: per-row "Last trade" column. */}
            <th
              role="columnheader"
              scope="col"
              className="text-text-tertiary uppercase text-[7.5px] select-none px-1 py-0.5"
            >
              {LAST_TRADE_LABEL}
            </th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((s) => (
            <tr key={s.symbol} role="row" className="border-t border-border">
              <td className="px-1 py-0.5">{s.symbol}</td>
              <td className="px-1 py-0.5 text-right">{s.trades}</td>
              <td className="px-1 py-0.5 text-right">{fmtPct(s.win_rate)}</td>
              <td className="px-1 py-0.5 text-right">{s.avg_rr.toFixed(2)}</td>
              <td className={`px-1 py-0.5 text-right ${pnlClass(s.pnl_usdt)}`}>
                {fmtPnl(s.pnl_usdt)}
              </td>
              <td className="px-1 py-0.5 text-right">{fmtNum(s.sharpe_annualized, 2)}</td>
              {/* PR10.6 T-UI.3 completion: per-row relative-time stamp. */}
              <td className="px-1 py-0.5 text-right text-text-tertiary">
                {fmtRelativeTime(s.last_trade_closed_at)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {/* T-UI.3 / PR10.5: card-level "computed" clock — kept since it's
          per-card (not per-row). The card-level "last trade" was misleading
          (only showed sorted[0]'s value) and is replaced by the per-row
          column above. */}
      {sorted[0]?.computed_at && (
        <div className="mt-1 text-[8px] text-text-tertiary text-right">
          computed {new Date(sorted[0].computed_at).toLocaleTimeString()}
        </div>
      )}
    </Panel>
  );
}
