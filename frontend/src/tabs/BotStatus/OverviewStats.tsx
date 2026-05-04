import { Panel } from "@/components/ui/Panel";
import { useBotOverview } from "./hooks/useBotOverview";
import type { WindowStats } from "@/lib/api";

function fmtPnl(v: number | null | undefined): string {
  if (v == null) return "—";
  const sign = v >= 0 ? "+" : "-";
  return `${sign}$${Math.abs(v).toFixed(2)}`;
}

function fmtPct(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${Math.round(v * 100)}%`;
}

function fmtNum(v: number | null | undefined, dp = 2): string {
  if (v == null) return "—";
  return v.toFixed(dp);
}

function pnlClass(v: number | null | undefined): string {
  if (v == null) return "text-text-secondary";
  return v >= 0 ? "text-green" : "text-red";
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

function PnlRow({ label, stats }: { label: string; stats: WindowStats | null }) {
  return (
    <div className="flex justify-between">
      <span className="text-text-secondary">{label}</span>
      <span className={pnlClass(stats?.pnl_usdt)}>
        {fmtPnl(stats?.pnl_usdt)}
      </span>
    </div>
  );
}

export function OverviewStats() {
  const { data, error, loading, refetch } = useBotOverview();

  if (error) {
    return (
      <Panel title="Overview" intensity="alert" rightSlot={<RefreshButton onClick={refetch} />}>
        <span className="text-red">Error: {error}</span>
      </Panel>
    );
  }

  const o = data;
  const wins30d =
    o?.last_30d != null
      ? Math.round(o.last_30d.win_rate * o.last_30d.trades)
      : null;

  return (
    <Panel title="Overview" rightSlot={<RefreshButton onClick={refetch} />}>
      <div className="grid grid-cols-1 gap-y-1">
        <PnlRow label="24h P&L" stats={loading || !o ? null : o.last_24h} />
        <PnlRow label="7d P&L" stats={loading || !o ? null : o.last_7d} />
        <PnlRow label="30d P&L" stats={loading || !o ? null : o.last_30d} />
        <div className="flex justify-between">
          <span className="text-text-secondary">Win rate (30d)</span>
          <span>
            {o
              ? `${fmtPct(o.last_30d.win_rate)} (${wins30d}/${o.last_30d.trades})`
              : "—"}
          </span>
        </div>
        {o ? (
          <div className="text-[8px] text-text-tertiary text-right">
            {`Long: ${fmtPct(o.long_only_30d.win_rate)} Short: ${fmtPct(o.short_only_30d.win_rate)}`}
          </div>
        ) : null}
        <div className="flex justify-between border-t border-border pt-1 mt-1">
          <span className="text-text-secondary">Sharpe / PF / DD</span>
          <span>
            {o
              ? `${fmtNum(o.last_30d.sharpe_annualized, 2)} / ${fmtNum(o.last_30d.profit_factor, 2)} / ${fmtNum(o.last_30d.max_drawdown * 100, 1)}%`
              : "—"}
          </span>
        </div>
      </div>
    </Panel>
  );
}
