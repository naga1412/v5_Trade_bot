import { Panel } from "@/components/ui/Panel";
import type { LivePrediction } from "@/lib/api";

const fmt = (v: number | null) => (v == null ? "—" : v.toFixed(2));

export function TradeSetup({ data }: { data: LivePrediction | null }) {
  const ts = data?.trade_setup;
  return (
    <Panel title="Trade Setup">
      <div className="grid grid-cols-2 gap-x-2 gap-y-1">
        <span className="text-text-secondary">Entry</span>
        <span className="text-right">{fmt(ts?.entry ?? null)}</span>
        <span className="text-text-secondary">Stop</span>
        <span className="text-right text-red">{fmt(ts?.stop_loss ?? null)}</span>
        <span className="text-text-secondary">TP</span>
        <span className="text-right text-green">{fmt(ts?.take_profit ?? null)}</span>
        <span className="text-text-secondary">R:R</span>
        <span className="text-right">{fmt(ts?.risk_reward ?? null)}</span>
      </div>
    </Panel>
  );
}
