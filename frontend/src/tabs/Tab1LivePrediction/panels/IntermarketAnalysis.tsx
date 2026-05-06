import { useQuery } from "@tanstack/react-query";

import { Panel } from "@/components/ui/Panel";
import { getIntermarket } from "@/lib/api";
import type { LivePrediction } from "@/lib/api";


function corrColorClass(c: number | null): string {
  if (c === null) return "text-text-tertiary";
  return Math.abs(c) > 0.5 ? "text-red-400" : "text-text-secondary";
}


export function IntermarketAnalysis({ data }: { data: LivePrediction | null }) {
  const symbol = data?.symbol;
  const { data: snap, isLoading, isError } = useQuery({
    queryKey: ["intermarket", symbol],
    queryFn: () => getIntermarket(symbol!),
    enabled: Boolean(symbol),
    staleTime: 30_000,
  });

  const ready = !isLoading && !isError && snap !== undefined;
  const dxy = ready ? snap.dxy_correlation_30d : null;
  const gold = ready ? snap.gold_correlation_30d : null;

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
