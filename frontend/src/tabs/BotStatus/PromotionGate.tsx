import { Panel } from "@/components/ui/Panel";
import { usePromotionGate } from "./hooks/usePromotionGate";
import type { GateMetric } from "@/lib/api";

function progressRatio(metric: GateMetric): number {
  if (metric.current == null) return 0;
  if (metric.threshold === 0) return metric.passing ? 1 : 0;
  if (metric.operator === ">=") {
    return Math.max(0, Math.min(1, metric.current / metric.threshold));
  }
  // <=  e.g. drawdown  → ratio = how far over the threshold (clamped 0..1).
  // Show "shrinking" toward threshold: when current <= threshold, fully passing (1).
  // When over, ratio shrinks from 1 toward 0 as we exceed the threshold.
  if (metric.current <= metric.threshold) return 1;
  // current > threshold → distance over threshold
  return Math.max(0, Math.min(1, metric.threshold / metric.current));
}

function fmtMetric(metric: GateMetric): string {
  if (metric.current == null) return "—";
  // Heuristic: ratios (≤ 1) shown as %; integers shown as number
  if (Math.abs(metric.current) <= 1 && Math.abs(metric.threshold) <= 1) {
    return `${(metric.current * 100).toFixed(1)}%`;
  }
  return metric.current.toFixed(2);
}

function fmtThreshold(metric: GateMetric): string {
  if (Math.abs(metric.threshold) <= 1) {
    return `${(metric.threshold * 100).toFixed(1)}%`;
  }
  return metric.threshold.toFixed(2);
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

function MetricRow({ metric }: { metric: GateMetric }) {
  const ratio = progressRatio(metric);
  return (
    <div className="grid grid-cols-[1fr_auto_auto_1.2fr] gap-x-2 items-center">
      <span className="text-text-secondary truncate">{metric.name}</span>
      <span className="text-right">{fmtMetric(metric)}</span>
      <span className="text-text-tertiary text-right">
        {metric.operator} {fmtThreshold(metric)}
      </span>
      {metric.passing ? (
        <span aria-label={`passing ${metric.name}`} className="text-green text-right">✓</span>
      ) : (
        <div
          role="progressbar"
          aria-label={`progress ${metric.name}`}
          aria-valuenow={Math.round(ratio * 100)}
          aria-valuemin={0}
          aria-valuemax={100}
          className="h-1 bg-bg-elevated rounded"
        >
          <div
            className="h-1 rounded bg-gold"
            style={{ width: `${ratio * 100}%` }}
          />
        </div>
      )}
    </div>
  );
}

export function PromotionGate() {
  const { data, error, refetch } = usePromotionGate();

  if (error) {
    return (
      <Panel title="Promotion Gate" intensity="alert" rightSlot={<RefreshButton onClick={refetch} />}>
        <span className="text-red">Error: {error}</span>
      </Panel>
    );
  }

  if (!data) {
    return (
      <Panel title="Promotion Gate" rightSlot={<RefreshButton onClick={refetch} />}>
        <span>—</span>
      </Panel>
    );
  }

  return (
    <Panel title="Promotion Gate" rightSlot={<RefreshButton onClick={refetch} />}>
      <div className="flex flex-col gap-1">
        {data.metrics.map((m) => (
          <MetricRow key={m.name} metric={m} />
        ))}
      </div>
      <div className="mt-1 pt-1 border-t border-border italic text-[8px] text-text-tertiary">
        {data.distance_summary}
        <span className="ml-2">→ {data.target_mode}</span>
      </div>
    </Panel>
  );
}
