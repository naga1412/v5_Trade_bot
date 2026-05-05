import { Panel } from "@/components/ui/Panel";
import type { LivePrediction } from "@/lib/api";

const fmt = (v: number | null, dp = 2) =>
  v == null ? "—" : v.toFixed(dp);

// Panel #8 — RSI / MACD / Stoch / CCI 2-col grid (per MASTER_PLAN §9 line 285).
// Stoch + CCI are placeholders until the backend exposes them; SP-6 ships
// the slot wired to em-dash so the layout matches the spec immediately.
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
        <span className="text-text-secondary">Stoch</span>
        <span className="text-right text-text-tertiary">—</span>
        <span className="text-text-secondary">CCI</span>
        <span className="text-right text-text-tertiary">—</span>
      </div>
    </Panel>
  );
}
