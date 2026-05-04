import { Panel } from "@/components/ui/Panel";
import type { LivePrediction } from "@/lib/api";

interface Props {
  data: LivePrediction | null;
}

const labelFor = (score: number): "BULL" | "BEAR" | "NEUTRAL" => {
  if (score > 0.10) return "BULL";
  if (score < -0.10) return "BEAR";
  return "NEUTRAL";
};

export function MasterBiasScore({ data }: Props) {
  if (!data) return <Panel title="Master Bias Score">—</Panel>;
  const score = data.final.score;
  const pct = ((score + 1) / 2) * 100;
  const label = labelFor(score);
  const trackColor =
    label === "BULL" ? "bg-green" : label === "BEAR" ? "bg-red" : "bg-purple";

  // SP-1: ghost candle preview (rendered only when ghost is non-null).
  const ghost = data.ghost ?? null;
  const ghostUsd = ghost ? Math.round(ghost.uncertainty * data.price) : 0;
  const ghostPct = ghost ? (ghost.uncertainty * 100).toFixed(2) : "0";
  const deltaPct = ghost
    ? (((ghost.close - ghost.open) / ghost.open) * 100).toFixed(2)
    : "0";
  const deltaUp = ghost ? ghost.close >= ghost.open : false;

  return (
    <Panel title="Master Bias Score">
      <div className="flex justify-between mb-1">
        <span>{(score * 100).toFixed(1)}</span>
        <span className="text-text-secondary">{label}</span>
      </div>
      <div className="h-1 bg-bg-elevated rounded">
        <div
          className={`h-1 rounded ${trackColor}`}
          style={{ width: `${pct}%` }}
          aria-label={`bias ${score.toFixed(2)}`}
        />
      </div>
      {ghost && (
        <div className="mt-3 pt-3 border-t border-border">
          <div className="flex justify-between text-xs uppercase text-text-secondary tracking-wide">
            <span>Ghost Candle</span>
            <span>
              ±${ghostUsd} / {ghostPct}%
            </span>
          </div>
          <div className="text-xs mt-1 font-mono">
            Open ${ghost.open.toFixed(2)} → Close ${ghost.close.toFixed(2)}{" "}
            <span className={deltaUp ? "text-green" : "text-red"}>
              ({deltaUp ? "+" : ""}
              {deltaPct}%)
            </span>
          </div>
        </div>
      )}
    </Panel>
  );
}
