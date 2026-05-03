import { useState } from "react";
import { Panel } from "@/components/ui/Panel";
import { useRecentTrades } from "./hooks/useRecentTrades";
import type { RecentTrade, RecentTradesFilters } from "@/lib/api";

function fmtTime(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  // MM-DD HH:MM (short relative format)
  const mm = String(d.getUTCMonth() + 1).padStart(2, "0");
  const dd = String(d.getUTCDate()).padStart(2, "0");
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mi = String(d.getUTCMinutes()).padStart(2, "0");
  return `${mm}-${dd} ${hh}:${mi}`;
}

function fmtPnl(v: number): string {
  const sign = v >= 0 ? "+" : "-";
  return `${sign}$${Math.abs(v).toFixed(2)}`;
}

function fmtPct(v: number): string {
  const sign = v >= 0 ? "+" : "";
  return `${sign}${v.toFixed(2)}%`;
}

function pnlClass(v: number): string {
  return v >= 0 ? "text-green" : "text-red";
}

function reasonBadge(reason: RecentTrade["exit_reason"]): { label: string; cls: string } {
  switch (reason) {
    case "TAKE_PROFIT": return { label: "TP", cls: "text-green" };
    case "STOP_LOSS": return { label: "SL", cls: "text-red" };
    case "TIMEOUT": return { label: "TIMEOUT", cls: "text-text-tertiary" };
  }
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

export function RecentTrades() {
  const { data, error, filters, setFilters, refetch } = useRecentTrades();
  const [symbolInput, setSymbolInput] = useState<string>(filters.symbol ?? "");

  function update(patch: Partial<RecentTradesFilters>) {
    const next: RecentTradesFilters = { ...filters, ...patch };
    // Strip empty optional values
    if (next.symbol === "") delete next.symbol;
    if (next.direction === undefined) delete next.direction;
    if (next.result === undefined) delete next.result;
    setFilters(next);
  }

  if (error) {
    return (
      <Panel title="Recent Trades" intensity="alert" rightSlot={<RefreshButton onClick={refetch} />}>
        <span className="text-red">Error: {error}</span>
      </Panel>
    );
  }

  return (
    <Panel title="Recent Trades" rightSlot={<RefreshButton onClick={refetch} />}>
      <div className="flex flex-wrap gap-1 mb-1 items-center">
        <label className="flex items-center gap-1">
          <span className="text-text-tertiary text-[8px]">Direction</span>
          <select
            aria-label="direction filter"
            className="bg-bg-elevated text-text-primary px-1 py-0.5 rounded border border-border text-[9px]"
            value={filters.direction ?? ""}
            onChange={(e) => {
              const v = e.target.value;
              update({ direction: v === "" ? undefined : (v as "LONG" | "SHORT") });
            }}
          >
            <option value="">All</option>
            <option value="LONG">LONG</option>
            <option value="SHORT">SHORT</option>
          </select>
        </label>
        <label className="flex items-center gap-1">
          <span className="text-text-tertiary text-[8px]">Result</span>
          <select
            aria-label="result filter"
            className="bg-bg-elevated text-text-primary px-1 py-0.5 rounded border border-border text-[9px]"
            value={filters.result ?? ""}
            onChange={(e) => {
              const v = e.target.value;
              update({ result: v === "" ? undefined : (v as "win" | "loss") });
            }}
          >
            <option value="">All</option>
            <option value="win">Win</option>
            <option value="loss">Loss</option>
          </select>
        </label>
        <label className="flex items-center gap-1">
          <span className="text-text-tertiary text-[8px]">Symbol</span>
          <input
            aria-label="symbol filter"
            type="text"
            placeholder="e.g. BTC/USDT"
            className="bg-bg-elevated text-text-primary px-1 py-0.5 rounded border border-border text-[9px] w-[90px]"
            value={symbolInput}
            onChange={(e) => setSymbolInput(e.target.value)}
            onBlur={() => update({ symbol: symbolInput })}
            onKeyDown={(e) => { if (e.key === "Enter") update({ symbol: symbolInput }); }}
          />
        </label>
      </div>

      {!data ? (
        <span>—</span>
      ) : data.length === 0 ? (
        <span className="text-text-secondary">No trades match filters</span>
      ) : (
        <table role="table" className="w-full text-left">
          <thead>
            <tr role="row">
              <th role="columnheader" className="text-text-tertiary uppercase text-[7.5px] px-1">Time</th>
              <th role="columnheader" className="text-text-tertiary uppercase text-[7.5px] px-1">Symbol</th>
              <th role="columnheader" className="text-text-tertiary uppercase text-[7.5px] px-1">Dir</th>
              <th role="columnheader" className="text-text-tertiary uppercase text-[7.5px] px-1 text-right">Entry</th>
              <th role="columnheader" className="text-text-tertiary uppercase text-[7.5px] px-1 text-right">Exit</th>
              <th role="columnheader" className="text-text-tertiary uppercase text-[7.5px] px-1 text-right">P&L</th>
              <th role="columnheader" className="text-text-tertiary uppercase text-[7.5px] px-1">Why</th>
            </tr>
          </thead>
          <tbody>
            {data.map((t) => {
              const badge = reasonBadge(t.exit_reason);
              return (
                <tr key={t.signal_id} role="row" className="border-t border-border">
                  <td className="px-1 py-0.5">
                    <a
                      href={`#/live-prediction?signal=${encodeURIComponent(t.signal_id)}`}
                      className="text-cyan hover:underline"
                    >
                      {fmtTime(t.closed_at)}
                    </a>
                  </td>
                  <td className="px-1 py-0.5">{t.symbol}</td>
                  <td className={`px-1 py-0.5 ${t.direction === "LONG" ? "text-green" : "text-red"}`}>
                    {t.direction === "LONG" ? "↗" : "↘"}
                  </td>
                  <td className="px-1 py-0.5 text-right">{t.entry_price.toFixed(2)}</td>
                  <td className="px-1 py-0.5 text-right">{t.exit_price.toFixed(2)}</td>
                  <td className={`px-1 py-0.5 text-right ${pnlClass(t.pnl_usdt)}`}>
                    {`${fmtPct(t.pnl_pct)} / ${fmtPnl(t.pnl_usdt)}`}
                  </td>
                  <td className={`px-1 py-0.5 ${badge.cls}`}>{badge.label}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </Panel>
  );
}
