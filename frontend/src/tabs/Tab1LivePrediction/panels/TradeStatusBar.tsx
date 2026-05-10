import { Panel } from "@/components/ui/Panel";
import type { LivePrediction } from "@/lib/api";

interface Props {
  data: LivePrediction | null;
}

const COLOR = {
  LONG: "text-green",
  SHORT: "text-red",
  NEUTRAL: "text-text-secondary",
} as const;

export function TradeStatusBar({ data }: Props) {
  if (!data || !data.final) return <Panel title="Trade Status">—</Panel>;
  const dir = data.final.direction ?? "NEUTRAL";
  return (
    <Panel title="Trade Status" intensity={dir !== "NEUTRAL" ? "alert" : "default"}>
      <div className="flex justify-between">
        <span className={COLOR[dir]}>{dir}</span>
        <span className="text-text-secondary">
          {data.cold_start ? "warming" : "live"}
        </span>
      </div>
    </Panel>
  );
}
