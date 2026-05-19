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
            </tr>
          ))}
        </tbody>
      </table>
      {/* T-UI.3 / PR10.5: footer surfaces the server "computed" clock and
          the most-recent closed trade so operators can tell a stale
          window from a quiet one. Guarded on `computed_at` so we don't
          crash mid-rollout against an older backend. */}
      {sorted[0]?.computed_at && (
        <div className="mt-1 text-[8px] text-text-tertiary text-right">
          computed {new Date(sorted[0].computed_at).toLocaleTimeString()}
          {sorted[0].last_trade_closed_at && (
            <>
              {" · last trade "}
              {new Date(sorted[0].last_trade_closed_at).toLocaleTimeString()}
            </>
          )}
        </div>
      )}
    </Panel>
  );
}
