import { Panel } from "@/components/ui/Panel";
import type { LivePrediction } from "@/lib/api";

// Panel #5 — Conv-LSTM supervisor (layer 8).
// Hidden entirely when L8 is missing — this is an alert tile, not a
// permanent slot. When SHORT + confidence > 0.75, the Panel border
// switches to the alert (red) variant.
export function DeepLearningSupervisor({ data }: { data: LivePrediction | null }) {
  const l8 = data?.layer_scores?.["8"] ?? null;
  if (!l8) return null;
  const isShortAlert = l8.direction === "SHORT" && l8.confidence > 0.75;
  const dirCls =
    l8.direction === "LONG"
      ? "text-green"
      : l8.direction === "SHORT"
        ? "text-red"
        : "text-text-secondary";
  return (
    <Panel
      title="Deep Learning Supervisor"
      intensity={isShortAlert ? "alert" : "default"}
    >
      <div className="flex justify-between mb-1">
        <span className={dirCls}>{l8.direction}</span>
        <span className="text-text-secondary">
          {(l8.confidence * 100).toFixed(0)}%
        </span>
      </div>
      {isShortAlert && (
        <div className="text-red text-[8px] uppercase tracking-wide">
          75%+ SHORT alert
        </div>
      )}
      {l8.notes && (
        <div className="text-text-tertiary mt-1">{l8.notes}</div>
      )}
    </Panel>
  );
}
