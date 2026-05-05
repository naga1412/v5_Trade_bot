import { Panel } from "@/components/ui/Panel";
import type { LivePrediction } from "@/lib/api";

function classify(notes: string): "above_pdh" | "below_pdl" | "none" {
  if (/sweep above PDH/i.test(notes)) return "above_pdh";
  if (/sweep below PDL/i.test(notes)) return "below_pdl";
  return "none";
}

// Panel #10 — L4 SMC sweep (above PDH / below PDL) + future traps_fired
// "liquidity_sweep" alert. SP-6 tolerates absence of prediction_extras
// (deferred backend work) by reading via a structural cast.
export function LiquiditySweep({ data }: { data: LivePrediction | null }) {
  if (!data) return <Panel title="Liquidity Sweep">—</Panel>;
  const l4 = data.layer_scores?.["4"];
  if (!l4) {
    return (
      <Panel title="Liquidity Sweep">
        <span className="text-text-tertiary">no sweep</span>
      </Panel>
    );
  }
  const cls = classify(l4.notes);
  const trapsFired =
    (data as { prediction_extras?: { traps_fired?: string[] } })
      .prediction_extras?.traps_fired ?? [];
  const isTrap = trapsFired.includes("liquidity_sweep");
  return (
    <Panel
      title="Liquidity Sweep"
      intensity={isTrap ? "alert" : "default"}
    >
      {cls === "above_pdh" ? (
        <span className="text-green">Above PDH</span>
      ) : cls === "below_pdl" ? (
        <span className="text-red">Below PDL</span>
      ) : (
        <span className="text-text-tertiary">no sweep</span>
      )}
      {isTrap && (
        <div className="mt-1 text-red text-[8px] uppercase">trap fired</div>
      )}
    </Panel>
  );
}
