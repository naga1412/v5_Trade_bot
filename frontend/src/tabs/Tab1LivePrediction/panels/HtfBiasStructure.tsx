import { Panel } from "@/components/ui/Panel";
import type { LivePrediction } from "@/lib/api";

const PHASES = ["Accumulation", "Markup", "Distribution", "Markdown"] as const;

// L1 macro layer carries the HTF Wyckoff phase as a substring inside the
// notes field (free-text). Backend doesn't expose it as a structured field
// yet, so we sniff the substring and fall back to "unknown".
function extractPhase(notes: string): string {
  for (const p of PHASES) {
    if (notes.includes(p)) return p;
  }
  return "unknown";
}

// Panel #6 — HTF bias from layer 1 + parsed Wyckoff phase.
export function HtfBiasStructure({ data }: { data: LivePrediction | null }) {
  if (!data) return <Panel title="HTF Bias & Structure">—</Panel>;
  const l1 = data.layer_scores?.["1"];
  if (!l1) return <Panel title="HTF Bias & Structure">—</Panel>;
  const dirCls =
    l1.direction === "LONG"
      ? "text-green"
      : l1.direction === "SHORT"
        ? "text-red"
        : "text-text-secondary";
  return (
    <Panel title="HTF Bias & Structure">
      <div className="grid grid-cols-2 gap-x-2 gap-y-1">
        <span className="text-text-secondary">Direction</span>
        <span className={`text-right ${dirCls}`}>{l1.direction}</span>
        <span className="text-text-secondary">Wyckoff</span>
        <span className="text-right">{extractPhase(l1.notes)}</span>
        <span className="text-text-secondary">Confidence</span>
        <span className="text-right">{(l1.confidence * 100).toFixed(0)}%</span>
      </div>
    </Panel>
  );
}
