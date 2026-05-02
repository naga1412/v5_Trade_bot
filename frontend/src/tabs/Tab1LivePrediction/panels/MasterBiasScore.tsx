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
    </Panel>
  );
}
