import { Panel } from "@/components/ui/Panel";
import type { LivePrediction } from "@/lib/api";

const fmt = (v: number | null, dp = 2) => (v == null ? "—" : v.toFixed(dp));

// Panel #3 — risk:reward, match-strict %, max-drawdown placeholder.
// Backend doesn't expose realized drawdown until SP-7, so the Max DD cell
// always renders an em-dash for SP-6.
export function FinalValue({ data }: { data: LivePrediction | null }) {
  if (!data) return <Panel title="Final Value">—</Panel>;
  const rr = data.trade_setup.risk_reward;
  const match = data.final.confidence * 100;
  return (
    <Panel title="Final Value">
      <div className="grid grid-cols-2 gap-x-2 gap-y-1">
        <span className="text-text-secondary">Risk:Reward</span>
        <span className="text-right">{fmt(rr, 1)}</span>
        <span className="text-text-secondary">Match strict</span>
        <span className="text-right">{match.toFixed(0)}%</span>
        <span className="text-text-secondary">Max DD</span>
        <span className="text-right text-text-tertiary">—</span>
      </div>
    </Panel>
  );
}
