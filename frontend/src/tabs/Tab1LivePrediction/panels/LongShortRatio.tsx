import { Panel } from "@/components/ui/Panel";
import type { LivePrediction, LayerScore } from "@/lib/api";

// Counts LONG/SHORT directions across all populated layer_scores entries.
// NEUTRAL and null entries are excluded from numerator + denominator.
function counts(scores: Record<string, LayerScore | null>): { l: number; s: number } {
  let l = 0;
  let s = 0;
  for (const v of Object.values(scores)) {
    if (!v) continue;
    if (v.direction === "LONG") l++;
    else if (v.direction === "SHORT") s++;
  }
  return { l, s };
}

// Panel #4 — split-bar visualization of bullish vs bearish layer count.
// Empty fixture → 50/50 placeholder so the bar still renders centered.
export function LongShortRatio({ data }: { data: LivePrediction | null }) {
  if (!data) return <Panel title="Long / Short Ratio">—</Panel>;
  const { l, s } = counts(data.layer_scores);
  const total = l + s;
  const lp = total === 0 ? 50 : (l / total) * 100;
  const sp = 100 - lp;
  return (
    <Panel title="Long / Short Ratio">
      <div className="h-1 flex rounded overflow-hidden bg-bg-elevated">
        <div
          className="bg-green"
          style={{ width: `${lp}%` }}
          aria-label={`long ${lp.toFixed(1)}%`}
        />
        <div
          className="bg-red"
          style={{ width: `${sp}%` }}
          aria-label={`short ${sp.toFixed(1)}%`}
        />
      </div>
      <div className="mt-1 flex justify-between text-text-secondary">
        <span>{lp.toFixed(1)}</span>
        <span>{sp.toFixed(1)}</span>
      </div>
    </Panel>
  );
}
