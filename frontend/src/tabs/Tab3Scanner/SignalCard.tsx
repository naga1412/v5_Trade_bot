// SP-6 Phase D4: Tab 3 Scanner Radar — single SignalCard.
//
// 5-row layout per MASTER_PLAN §9 lines 325-330:
//   Row 1: ★ favorite + symbol + full_name | sparkline + ±points
//   Row 2: tag pills (LONG/SHORT, 4h ±, Confirmed/Probable, hybrid dot, AI ±score, Wyckoff phase)
//   Row 3: right-aligned ±%change
//   Row 4: confidence bar (cyan) + "Conf X%"
//   Row 5: 4 score chips (SMC, Wyckoff, Microstructure, Momentum)
//
// Sparkline mirrors SP-0.5 EquityCurve: 60×16 viewBox SVG polyline, no axes.
// The card is rendered as a <button> so the entire surface is keyboard-focusable
// — clicking it invokes the optional `onClick(card)` prop. Hash-route navigation
// to Tab 1 lives in BullishColumn / BearishColumn (D5/D8), not here.

import type { SignalCard as SignalCardData } from "@/lib/api";

const VB_W = 60;
const VB_H = 16;

function buildSparkPath(values: number[]): string | null {
  if (values.length < 2) return null;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const stepX = VB_W / (values.length - 1);
  let d = "";
  values.forEach((v, i) => {
    const x = i * stepX;
    const y = VB_H - ((v - min) / range) * VB_H;
    d += (i === 0 ? "M " : " L ") + x.toFixed(2) + " " + y.toFixed(2);
  });
  return d;
}

function tierBadge(
  tier: SignalCardData["signal_tier"],
): { label: string; cls: string } | null {
  if (tier === "STANDARD" || tier === "A+") {
    return { label: "✓ CONFIRMED", cls: "text-green border-green/30" };
  }
  if (tier === "PAPER" || tier === "SMALL") {
    return { label: "~ PROBABLE", cls: "text-orange border-orange/30" };
  }
  return null;
}

function fmtSigned(n: number, suffix = ""): string {
  return (n >= 0 ? `+${n}` : `${n}`) + suffix;
}

function fmtPct(n: number): string {
  const sign = n >= 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}%`;
}

interface Props {
  card: SignalCardData;
  onClick?: (card: SignalCardData) => void;
}

export function SignalCard({ card, onClick }: Props) {
  const dirCls =
    card.direction === "LONG" ? "bg-green/15 text-green" : "bg-red/15 text-red";
  const htfCls =
    card.htf_direction === "LONG"
      ? "border-green text-green"
      : card.htf_direction === "SHORT"
        ? "border-red text-red"
        : "border-text-tertiary text-text-tertiary";
  const badge = tierBadge(card.signal_tier);
  const sparkD = buildSparkPath(card.sparkline);
  const stroke = card.direction === "LONG" ? "var(--green)" : "var(--red)";
  const pointsTxt = fmtSigned(card.points);
  const pctTxt = fmtPct(card.pct_change);
  const aiScoreTxt = fmtSigned(card.ai_score, "").replace(/^([+-])/, "AI $1");

  const staleTooltip = card.is_stale && card.ts
    ? `Prediction older than 2× timeframe (ts=${card.ts})`
    : undefined;

  return (
    <button
      type="button"
      onClick={() => onClick?.(card)}
      data-testid={`signal-card-${card.symbol}`}
      title={staleTooltip}
      aria-label={card.is_stale ? `${card.symbol} (stale)` : undefined}
      className={`block w-full text-left bg-bg-panel border border-border rounded p-2 mb-2 hover:bg-bg-elevated focus:outline-none focus:ring-1 focus:ring-cyan ${card.is_stale ? "opacity-60" : ""}`}
    >
      {/* Row 1 — favorite + symbol + name | sparkline + points */}
      <div className="flex justify-between items-center mb-1">
        <div className="flex items-center gap-1">
          <span
            className={card.is_favorite ? "text-gold" : "text-text-tertiary"}
            aria-label={card.is_favorite ? "favorite" : "not favorite"}
          >
            ★
          </span>
          <span className="font-semibold text-[11.5px]">{card.symbol}</span>
          <span className="text-text-tertiary text-[9px]">{card.full_name}</span>
        </div>
        <div className="flex items-center gap-1">
          {sparkD && (
            <svg
              viewBox={`0 0 ${VB_W} ${VB_H}`}
              preserveAspectRatio="none"
              className="w-[60px] h-[16px]"
              role="img"
              aria-label="sparkline"
            >
              <path d={sparkD} fill="none" stroke={stroke} strokeWidth="1" />
            </svg>
          )}
          <span
            className={`text-[9px] ${card.points >= 0 ? "text-green" : "text-red"}`}
          >
            {pointsTxt}
          </span>
        </div>
      </div>

      {/* Row 2 — tag pills */}
      <div className="flex flex-wrap items-center gap-1 mb-1">
        <span className={`px-1 rounded text-[9px] font-medium ${dirCls}`}>
          {card.direction}
        </span>
        <span
          className={`px-1 rounded text-[9px] font-medium border ${htfCls}`}
        >
          4h {card.htf_direction}
        </span>
        {badge && (
          <span
            className={`px-1 rounded text-[9px] font-medium border ${badge.cls}`}
          >
            {badge.label}
          </span>
        )}
        {card.is_stale && (
          <span
            data-testid={`signal-card-stale-${card.symbol}`}
            className="px-1 rounded text-[9px] font-medium border text-text-tertiary border-text-tertiary/40"
          >
            STALE
          </span>
        )}
        {card.hybrid_flag && (
          <span
            className="w-2 h-2 rounded-full inline-block bg-pink"
            aria-label={`hybrid ${card.hybrid_flag}`}
          />
        )}
        <span className="text-purple text-[9px] font-medium">{aiScoreTxt}</span>
        <span className="text-text-secondary text-[9px]">
          {card.wyckoff_phase}
        </span>
      </div>

      {/* Row 3 — ±%change right-aligned */}
      <div className="text-right text-[10px] mb-1">
        <span className={card.pct_change >= 0 ? "text-green" : "text-red"}>
          {pctTxt}
        </span>
      </div>

      {/* Row 4 — confidence bar + label */}
      <div className="flex items-center gap-2 mb-1">
        <div className="flex-1 h-1 bg-bg-elevated rounded overflow-hidden">
          <div
            className="h-1 bg-cyan rounded"
            style={{ width: `${card.confidence}%` }}
          />
        </div>
        <span className="text-text-secondary text-[9px]">
          Conf {card.confidence}%
        </span>
      </div>

      {/* Row 5 — score chips */}
      <div className="flex flex-wrap gap-1 text-[8px]">
        {(["smc", "wyckoff", "microstructure", "momentum"] as const).map((k) => {
          const v = card.scores[k];
          return (
            <span
              key={k}
              className="px-1 rounded bg-bg-elevated text-text-secondary uppercase"
            >
              {k} {fmtSigned(v)}
            </span>
          );
        })}
      </div>
    </button>
  );
}
