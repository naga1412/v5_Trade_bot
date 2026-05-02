import { Panel } from "@/components/ui/Panel";
import type { LivePrediction } from "@/lib/api";

const fmt = (v: number | null, dp = 2) =>
  v == null ? "—" : v.toFixed(dp);

export function MomentumIndicators({ data }: { data: LivePrediction | null }) {
  const m = data?.momentum;
  return (
    <Panel title="Momentum">
      <div className="grid grid-cols-2 gap-x-2 gap-y-1">
        <span className="text-text-secondary">RSI(14)</span>
        <span className="text-right">{fmt(m?.rsi ?? null, 1)}</span>
        <span className="text-text-secondary">MACD line</span>
        <span className="text-right">{fmt(m?.macd_line ?? null, 4)}</span>
        <span className="text-text-secondary">MACD signal</span>
        <span className="text-right">{fmt(m?.macd_signal ?? null, 4)}</span>
        <span className="text-text-secondary">MACD hist</span>
        <span className="text-right">{fmt(m?.macd_hist ?? null, 4)}</span>
      </div>
    </Panel>
  );
}
