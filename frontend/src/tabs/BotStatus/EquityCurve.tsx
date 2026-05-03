import { Panel } from "@/components/ui/Panel";
import { useEquityCurve } from "./hooks/useEquityCurve";
import type { EquityCurvePoint } from "@/lib/api";

const VB_W = 200;
const VB_H = 50;

interface PathParts {
  d: string;
  positive: boolean;
}

function buildPath(points: EquityCurvePoint[]): PathParts | null {
  if (points.length === 0) return null;
  const values = points.map((p) => p.cumulative_pnl_usdt);
  const min = Math.min(...values, 0);
  const max = Math.max(...values, 0);
  const range = max - min || 1;
  const stepX = points.length > 1 ? VB_W / (points.length - 1) : 0;

  const parts = points.map((p, i) => {
    const x = i * stepX;
    // Invert y so larger pnl is higher on the chart.
    const y = VB_H - ((p.cumulative_pnl_usdt - min) / range) * VB_H;
    return { x, y };
  });

  let d = `M ${parts[0]?.x.toFixed(2)} ${parts[0]?.y.toFixed(2)}`;
  for (let i = 1; i < parts.length; i++) {
    d += ` L ${parts[i]?.x.toFixed(2)} ${parts[i]?.y.toFixed(2)}`;
  }
  const last = values[values.length - 1] ?? 0;
  return { d, positive: last >= 0 };
}

function fmtPnl(v: number): string {
  const sign = v >= 0 ? "+" : "-";
  return `${sign}$${Math.abs(v).toFixed(2)}`;
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

export function EquityCurve() {
  const { data, error, refetch } = useEquityCurve();

  if (error) {
    return (
      <Panel title="Equity Curve" intensity="alert" rightSlot={<RefreshButton onClick={refetch} />}>
        <span className="text-red">Error: {error}</span>
      </Panel>
    );
  }

  if (!data) {
    return (
      <Panel title="Equity Curve" rightSlot={<RefreshButton onClick={refetch} />}>
        <span>—</span>
      </Panel>
    );
  }

  const pp = buildPath(data.points);
  const last = data.points.length > 0
    ? data.points[data.points.length - 1]?.cumulative_pnl_usdt ?? 0
    : 0;

  if (!pp || data.points.length === 0) {
    return (
      <Panel title="Equity Curve" rightSlot={<RefreshButton onClick={refetch} />}>
        <span className="text-text-secondary">No trades yet</span>
      </Panel>
    );
  }

  const stroke = pp.positive ? "var(--green)" : "var(--red)";

  return (
    <Panel title={`Equity Curve (${data.days}d)`} rightSlot={<RefreshButton onClick={refetch} />}>
      <div className="flex justify-between items-center mb-1">
        <span className="text-text-tertiary text-[8px]">{data.days}d Equity Curve</span>
        <span className={pp.positive ? "text-green" : "text-red"}>{fmtPnl(last)}</span>
      </div>
      <svg
        viewBox={`0 0 ${VB_W} ${VB_H}`}
        preserveAspectRatio="none"
        className="w-full h-[40px]"
        role="img"
        aria-label="equity curve sparkline"
      >
        <path d={pp.d} fill="none" stroke={stroke} strokeWidth="1" />
      </svg>
    </Panel>
  );
}
