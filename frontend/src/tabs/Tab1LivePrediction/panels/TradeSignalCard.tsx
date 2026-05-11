import type { LivePrediction } from "@/lib/api";

interface Props {
  data: LivePrediction | null;
}

const LAYER_LABEL: Record<string, string> = {
  layer1_htf_bias: "HTF Bias",
  layer2_pattern: "Patterns",
  layer3_microstructure: "Microstructure",
  layer4_volume: "Volume",
  layer5_momentum: "Momentum",
  layer6_liquidity: "Liquidity",
  layer7_oi_funding: "OI / Funding",
  layer8_dl: "Deep Learning",
  layer9_news: "News",
  smc: "SMC",
  wyckoff: "Wyckoff",
  momentum: "Momentum",
  microstructure: "Microstructure",
  volume: "Volume",
};

function labelFor(name: string): string {
  return LAYER_LABEL[name] ?? name.replace(/_/g, " ").replace(/^layer\d+_?/, "");
}

function signedBarColor(strength: number, direction: "LONG" | "SHORT" | "NEUTRAL"): string {
  if (direction === "LONG" && strength > 0) return "bg-green";
  if (direction === "SHORT" && strength > 0) return "bg-red";
  return "bg-text-tertiary/40";
}

function dirToSign(direction: "LONG" | "SHORT" | "NEUTRAL"): number {
  return direction === "LONG" ? 1 : direction === "SHORT" ? -1 : 0;
}

/**
 * Top-of-sidebar SMC-Pro-style summary card. Combines the most important
 * fields from the existing 17 panels into one prominent visual block so
 * the operator can grasp the current call without scrolling.
 *
 * Reuses data already in the LivePrediction response — no backend change.
 */
export function TradeSignalCard({ data }: Props) {
  if (!data || !data.final) {
    return (
      <section
        className="bg-bg-panel rounded-[4px] border border-border px-3 py-3 mb-1"
        aria-label="Trade Signal"
      >
        <div className="text-[7.5px] uppercase tracking-[0.04em] text-text-tertiary">
          Trade Signal
        </div>
        <div className="text-[10px] text-text-tertiary mt-2">—</div>
      </section>
    );
  }

  const dir = data.final.direction;
  const score = data.final.score ?? 0;        // -1..1
  const scoreDisplay = Math.round(score * 100); // -100..100
  const conf = data.final.confidence ?? 0;     // 0..1
  const confPct = Math.round(conf * 100);
  const rr = data.trade_setup?.risk_reward ?? null;
  const gaugePct = ((score + 1) / 2) * 100;

  const dirColor =
    dir === "LONG" ? "bg-green/15 text-green border-green/40"
      : dir === "SHORT" ? "bg-red/15 text-red border-red/40"
        : "bg-text-tertiary/15 text-text-secondary border-border";
  const dirBadge = dir === "LONG" ? "LONG" : dir === "SHORT" ? "SHORT" : "NEUTRAL";
  const trackColor = dir === "LONG" ? "bg-green" : dir === "SHORT" ? "bg-red" : "bg-purple";
  const highProb = conf >= 0.65 && Math.abs(score) >= 0.15;

  // Pick the top 6 contributing layers by absolute strength to render as
  // signed mini-bars. Filter out null entries (layer disabled / not yet
  // computed for this candle).
  const layerEntries = Object.entries(data.layer_scores ?? {})
    .flatMap(([k, v]) =>
      v == null
        ? []
        : [{
            name: k,
            strength: v.strength,
            direction: v.direction,
            confidence: v.confidence,
            signed: v.strength * dirToSign(v.direction),
          }],
    )
    .sort((a, b) => Math.abs(b.signed) - Math.abs(a.signed))
    .slice(0, 6);

  return (
    <section
      className="bg-bg-panel rounded-[4px] border border-border px-3 py-3 mb-1"
      aria-label="Trade Signal"
    >
      {/* Header row: title + direction badge */}
      <header className="flex items-center justify-between mb-2">
        <span className="text-[8px] uppercase tracking-[0.05em] text-text-tertiary">
          Trade Signal · {data.symbol} · {data.timeframe}
        </span>
        <span
          className={`px-2 py-0.5 text-[10px] font-bold rounded border ${dirColor}`}
        >
          {dirBadge}
        </span>
      </header>

      {/* Master Bias gauge */}
      <div className="mb-2">
        <div className="flex justify-between text-[8px] text-text-tertiary mb-1">
          <span>-100 Bear</span>
          <span>0 Neutral</span>
          <span>+100 Bull</span>
        </div>
        <div className="relative h-1.5 bg-bg-elevated rounded">
          <div className="absolute top-0 left-1/2 w-px h-1.5 bg-text-tertiary/50" />
          <div
            className={`h-1.5 rounded ${trackColor}`}
            style={{ width: `${gaugePct}%` }}
            aria-label={`master bias ${scoreDisplay}`}
          />
        </div>
        <div className="flex justify-between mt-1 text-[10px] font-mono">
          <span>AI Score</span>
          <span
            className={
              scoreDisplay > 0 ? "text-green" : scoreDisplay < 0 ? "text-red" : "text-text-secondary"
            }
          >
            {scoreDisplay > 0 ? "+" : ""}
            {scoreDisplay}
          </span>
        </div>
      </div>

      {/* Confidence + R/R row */}
      <div className="grid grid-cols-2 gap-2 mb-2">
        <div>
          <div className="text-[8px] text-text-tertiary mb-0.5">Confidence</div>
          <div className="h-1 bg-bg-elevated rounded">
            <div
              className={`h-1 rounded ${confPct >= 65 ? "bg-green" : "bg-purple"}`}
              style={{ width: `${confPct}%` }}
            />
          </div>
          <div className="text-[9px] font-mono mt-0.5">{confPct}%</div>
        </div>
        <div>
          <div className="text-[8px] text-text-tertiary mb-0.5">Risk : Reward</div>
          <div className="text-[10px] font-mono">
            {rr == null ? "—" : `1 : ${rr.toFixed(2)}`}
          </div>
        </div>
      </div>

      {/* High-prob setup hint */}
      {highProb && (
        <div className="mb-2 text-[9px] text-green">
          ✓ High-probability setup
        </div>
      )}

      {/* Per-layer signed mini-bars */}
      {layerEntries.length > 0 && (
        <div className="border-t border-border pt-2">
          <div className="text-[8px] uppercase tracking-[0.04em] text-text-tertiary mb-1">
            Layer Breakdown
          </div>
          <div className="space-y-1">
            {layerEntries.map((l) => {
              const absPct = Math.min(100, Math.abs(l.signed) * 100);
              return (
                <div key={l.name} className="grid grid-cols-[5rem_1fr_2rem] gap-1 items-center">
                  <span className="text-[9px] text-text-secondary truncate">
                    {labelFor(l.name)}
                  </span>
                  <div className="h-1 bg-bg-elevated rounded">
                    <div
                      className={`h-1 rounded ${signedBarColor(l.strength, l.direction)}`}
                      style={{ width: `${absPct}%` }}
                    />
                  </div>
                  <span
                    className={`text-[9px] font-mono text-right ${
                      l.direction === "LONG"
                        ? "text-green"
                        : l.direction === "SHORT"
                          ? "text-red"
                          : "text-text-tertiary"
                    }`}
                  >
                    {l.signed > 0 ? "+" : ""}
                    {Math.round(l.signed * 100)}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </section>
  );
}
