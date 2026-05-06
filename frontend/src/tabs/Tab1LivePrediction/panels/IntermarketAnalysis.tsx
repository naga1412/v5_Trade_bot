import { useEffect, useState } from "react";

import { Panel } from "@/components/ui/Panel";
import { getIntermarket } from "@/lib/api";
import type { IntermarketSnapshot, LivePrediction } from "@/lib/api";


function corrColorClass(c: number | null): string {
  if (c === null) return "text-text-tertiary";
  return Math.abs(c) > 0.5 ? "text-red-400" : "text-text-secondary";
}


export function IntermarketAnalysis({ data }: { data: LivePrediction | null }) {
  const symbol = data?.symbol;
  const [snap, setSnap] = useState<IntermarketSnapshot | null>(null);

  useEffect(() => {
    if (!symbol) {
      setSnap(null);
      return;
    }
    let cancelled = false;
    getIntermarket(symbol)
      .then((s) => { if (!cancelled) setSnap(s); })
      .catch(() => { if (!cancelled) setSnap(null); });
    return () => { cancelled = true; };
  }, [symbol]);

  const dxy = snap?.dxy_correlation_30d ?? null;
  const gold = snap?.gold_correlation_30d ?? null;

  return (
    <Panel title="Intermarket">
      <div className="grid grid-cols-2 gap-x-2 gap-y-1">
        <span className="text-text-secondary">DXY 30d</span>
        {dxy !== null ? (
          <span className={`text-right ${corrColorClass(dxy)}`}>
            {dxy.toFixed(2)}
          </span>
        ) : (
          <span className="text-right text-text-tertiary">no data</span>
        )}
        <span className="text-text-secondary">Gold 30d</span>
        {gold !== null ? (
          <span className={`text-right ${corrColorClass(gold)}`}>
            {gold.toFixed(2)}
          </span>
        ) : (
          <span className="text-right text-text-tertiary">no data</span>
        )}
      </div>
    </Panel>
  );
}
