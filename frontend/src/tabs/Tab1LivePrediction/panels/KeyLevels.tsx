import { Panel } from "@/components/ui/Panel";
import type { LivePrediction } from "@/lib/api";

// Panel #16 — Key levels (EMA20/EMA50/EMA200).
// Backend currently doesn't expose numeric EMA values via the predict
// endpoint — only L1 macro layer's free-text notes ("EMAs aligned
// ascending" / "Close above EMA200, EMAs mixed"). For SP-6 the slot is
// reserved with em-dash placeholders for the three EMA rows and the L1
// notes are surfaced as a context line. Backend extension is deferred.
export function KeyLevels({ data }: { data: LivePrediction | null }) {
  if (!data) return <Panel title="Key Levels">—</Panel>;
  const l1 = data.layer_scores?.["1"] ?? null;
  return (
    <Panel title="Key Levels">
      <div className="grid grid-cols-2 gap-x-2 gap-y-1">
        <span className="text-text-secondary">EMA20</span>
        <span className="text-right text-text-tertiary">—</span>
        <span className="text-text-secondary">EMA50</span>
        <span className="text-right text-text-tertiary">—</span>
        <span className="text-text-secondary">EMA200</span>
        <span className="text-right text-text-tertiary">—</span>
      </div>
      {l1?.notes && (
        <div className="mt-1 text-text-tertiary text-[8px] truncate">
          {l1.notes}
        </div>
      )}
    </Panel>
  );
}
