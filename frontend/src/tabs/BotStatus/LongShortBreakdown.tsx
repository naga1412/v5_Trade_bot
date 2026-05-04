import { Panel } from "@/components/ui/Panel";
import { useLongShortBreakdown } from "./hooks/useLongShortBreakdown";
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

function SideCard({ label, stats }: { label: string; stats: WindowStats | null }) {
  return (
    <div className="border border-border rounded p-1 bg-bg-elevated flex-1">
      <div className="text-text-tertiary uppercase text-[7.5px] mb-1">{label}</div>
      <div className="grid grid-cols-2 gap-x-1 gap-y-0.5">
        <span className="text-text-secondary">Trades</span>
        <span className="text-right">{stats ? stats.trades : "—"}</span>
        <span className="text-text-secondary">Win%</span>
        <span className="text-right">{fmtPct(stats?.win_rate)}</span>
        <span className="text-text-secondary">P&L</span>
        <span className={`text-right ${pnlClass(stats?.pnl_usdt)}`}>
          {fmtPnl(stats?.pnl_usdt)}
        </span>
        <span className="text-text-secondary">Avg PF</span>
        <span className="text-right">{fmtNum(stats?.profit_factor, 2)}</span>
      </div>
    </div>
  );
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

export function LongShortBreakdown() {
  const { data, error, refetch } = useLongShortBreakdown();

  if (error) {
    return (
      <Panel title="Long vs Short (30d)" intensity="alert" rightSlot={<RefreshButton onClick={refetch} />}>
        <span className="text-red">Error: {error}</span>
      </Panel>
    );
  }

  return (
    <Panel title="Long vs Short (30d)" rightSlot={<RefreshButton onClick={refetch} />}>
      <div className="flex flex-col md:flex-row gap-1">
        <SideCard label="LONG" stats={data ? data.long : null} />
        <SideCard label="SHORT" stats={data ? data.short : null} />
      </div>
    </Panel>
  );
}
