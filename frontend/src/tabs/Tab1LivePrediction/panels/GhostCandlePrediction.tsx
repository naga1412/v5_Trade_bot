import { Panel } from "@/components/ui/Panel";
import type { LivePrediction } from "@/lib/api";

// Panel #14 — predicted next-bar OHLC + uncertainty band (SP-1 Phase A).
// `data.ghost` is null when no active ML checkpoint is loaded — render a
// "no model" empty state so the slot is preserved per MASTER_PLAN §9.

// Pick a decimal precision that makes sense for the price's magnitude.
// SHIB at $0.00001 needs 8 decimals; BTC at $80000 needs 2.
function priceDecimals(price: number): number {
  if (price >= 100) return 2;
  if (price >= 1) return 4;
  if (price >= 0.01) return 6;
  return 8;
}

function fmtPrice(p: number, ref: number): string {
  return p.toFixed(priceDecimals(ref));
}

export function GhostCandlePrediction({ data }: { data: LivePrediction | null }) {
  const ghost = data?.ghost ?? null;
  if (!ghost) {
    return (
      <Panel title="Ghost Candle">
        <span className="text-text-tertiary">no model</span>
      </Panel>
    );
  }
  const price = data?.price ?? 0;
  const pctChange = price > 0 ? ((ghost.close - price) / price) * 100 : 0;
  const sign = pctChange >= 0 ? "+" : "";
  const pctCls =
    pctChange > 0 ? "text-green" : pctChange < 0 ? "text-red" : "text-text-secondary";
  const band = ghost.p95_high - ghost.p5_low;
  // Decimal precision keyed to the asset's scale so SHIB at $0.00001 doesn't
  // show "$0.00" (which the user reported as "ghost candle showing zero").
  const ref = Math.max(price, ghost.close);
  return (
    <Panel title="Ghost Candle">
      <div className="grid grid-cols-2 gap-x-2 gap-y-1">
        <span className="text-text-secondary">Predicted close</span>
        <span className="text-right">{fmtPrice(ghost.close, ref)}</span>
        <span className="text-text-secondary">% vs current</span>
        <span className={`text-right ${pctCls}`}>
          {sign}
          {pctChange.toFixed(2)}%
        </span>
        <span className="text-text-secondary">Band</span>
        <span className="text-right">{fmtPrice(band, ref)}</span>
        <span className="text-text-secondary">Uncertainty</span>
        <span className="text-right">{ghost.uncertainty.toFixed(2)}</span>
      </div>
    </Panel>
  );
}
