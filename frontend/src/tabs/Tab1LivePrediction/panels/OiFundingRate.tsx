import { useQuery } from "@tanstack/react-query";

import { Panel } from "@/components/ui/Panel";
import { getIntermarket } from "@/lib/api";
import type { LivePrediction } from "@/lib/api";


function fundingPctText(r: number): string {
  return `${(r * 100).toFixed(2)}%`;
}

function oiText(oi: number): string {
  if (oi >= 1e9) return `${(oi / 1e9).toFixed(2)}B`;
  if (oi >= 1e6) return `${(oi / 1e6).toFixed(1)}M`;
  return oi.toFixed(0);
}

function deltaText(d: number): string {
  return `${(d * 100).toFixed(1)}%`;
}


export function OiFundingRate({ data }: { data: LivePrediction | null }) {
  const symbol = data?.symbol;
  const { data: snap, isLoading, isError } = useQuery({
    queryKey: ["intermarket", symbol],
    queryFn: () => getIntermarket(symbol!),
    enabled: Boolean(symbol),
    staleTime: 30_000,
  });

  const ready = !isLoading && !isError && snap !== undefined;
  const funding = ready ? snap.funding_rate : null;
  const oi = ready ? snap.open_interest : null;
  const delta = ready ? snap.open_interest_delta_24h_pct : null;

  const fundingClass = funding !== null && funding < 0
    ? "text-red-400"
    : funding !== null
    ? "text-green-400"
    : "text-text-tertiary";
  const deltaClass = delta !== null && delta >= 0
    ? "text-green-400"
    : delta !== null
    ? "text-red-400"
    : "text-text-tertiary";

  return (
    <Panel title="OI & Funding Rate">
      <div className="grid grid-cols-2 gap-x-2 gap-y-1">
        <span className="text-text-secondary">Funding</span>
        {funding !== null ? (
          <span className={`text-right ${fundingClass}`}>
            {fundingPctText(funding)}
          </span>
        ) : (
          <span className="text-right text-text-tertiary">no data</span>
        )}
        <span className="text-text-secondary">OI</span>
        {oi !== null ? (
          <span className="text-right">{oiText(oi)}</span>
        ) : (
          <span className="text-right text-text-tertiary">no data</span>
        )}
        <span className="text-text-secondary">OI 24h</span>
        {delta !== null ? (
          <span className={`text-right ${deltaClass}`}>
            {delta >= 0 ? "▲" : "▼"} {deltaText(delta)}
          </span>
        ) : (
          <span className="text-right text-text-tertiary">no data</span>
        )}
      </div>
    </Panel>
  );
}
