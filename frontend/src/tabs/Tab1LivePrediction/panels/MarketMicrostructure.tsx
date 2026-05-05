import { Panel } from "@/components/ui/Panel";
import type { LivePrediction } from "@/lib/api";

function flowLabel(direction: "LONG" | "SHORT" | "NEUTRAL"): string {
  if (direction === "LONG") return "BUY DOM";
  if (direction === "SHORT") return "SELL DOM";
  return "BALANCED";
}

// Imbalance ratio is parsed from the L6 notes free-text — backend doesn't
// expose it as a structured field for SP-6.
function parseImbalance(notes: string): string {
  const m = notes.match(/imbalance=([0-9.]+)/);
  return m?.[1] ?? "—";
}

// Panel #9 — Order flow + imbalance + DOM label from L6 (microstructure).
export function MarketMicrostructure({ data }: { data: LivePrediction | null }) {
  if (!data) return <Panel title="Market Microstructure">—</Panel>;
  const l6 = data.layer_scores?.["6"];
  if (!l6) return <Panel title="Market Microstructure">—</Panel>;
  const flow = (l6.strength * (l6.direction === "SHORT" ? -1 : 1)).toFixed(2);
  return (
    <Panel title="Market Microstructure">
      <div className="grid grid-cols-2 gap-x-2 gap-y-1">
        <span className="text-text-secondary">Flow</span>
        <span className="text-right">{flow}</span>
        <span className="text-text-secondary">Imbalance</span>
        <span className="text-right">{parseImbalance(l6.notes)}</span>
        <span className="text-text-secondary">Label</span>
        <span className="text-right">{flowLabel(l6.direction)}</span>
      </div>
    </Panel>
  );
}
