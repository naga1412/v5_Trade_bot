import { useEffect, useState } from "react";
import { api, type FastScanRadar as FastScanRadarData, type FastScanCard } from "@/lib/api";
import { buildLivePredictionHash } from "./applyFilter";

interface Props {
  timeframe?: string;
  refreshIntervalMs?: number;
}

const TIER_COLOR: Record<string, string> = {
  confirmed: "bg-green/20 text-green border-green/40",
  probable: "bg-cyan-500/20 text-cyan-300 border-cyan-500/40",
  weak: "bg-yellow-500/15 text-yellow-300 border-yellow-500/30",
  diverging: "bg-orange-500/15 text-orange-300 border-orange-500/40",
  neutral: "bg-text-tertiary/10 text-text-tertiary border-border",
};

const PHASE_LABEL: Record<string, string> = {
  markup: "Markup",
  markdown: "Markdown",
  accumulation: "Accumulation",
  distribution: "Distribution",
  neutral: "Neutral",
};

function fmtPct(v: number | null, dp: number = 2): string {
  if (v == null) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(dp)}%`;
}

function ScanCardRow({
  card,
  side,
  onOpen,
}: {
  card: FastScanCard;
  side: "long" | "short";
  onOpen: (symbol: string) => void;
}) {
  const dirColor = side === "long" ? "text-green" : "text-red";
  const scoreColor =
    card.final_score > 0 ? "text-green"
      : card.final_score < 0 ? "text-red"
        : "text-text-secondary";
  return (
    <article
      className="bg-bg-panel rounded border border-border px-2 py-1.5 mb-1 hover:border-text-tertiary/40 transition-colors cursor-pointer focus:outline-none focus:border-text-tertiary"
      aria-label={`scan card ${card.symbol}`}
      role="button"
      tabIndex={0}
      onClick={() => onOpen(card.symbol)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen(card.symbol);
        }
      }}
    >
      <header className="flex items-center justify-between mb-1">
        <span className="font-mono text-[11px] font-semibold">{card.symbol}</span>
        <span
          className={`text-[8px] uppercase tracking-wide px-1.5 py-0.5 rounded border ${TIER_COLOR[card.tier] ?? TIER_COLOR.neutral}`}
        >
          {card.tier}
        </span>
      </header>
      <div className="grid grid-cols-[1fr_auto] gap-x-2 text-[10px] font-mono">
        <span className={dirColor}>
          {card.direction} · {PHASE_LABEL[card.phase] ?? card.phase}
        </span>
        <span className={scoreColor}>
          {card.final_score > 0 ? "+" : ""}
          {card.final_score.toFixed(0)}
        </span>
        <span className="text-text-tertiary">
          conf {(card.confidence * 100).toFixed(0)}%
          {" · move "}
          {fmtPct(card.expected_move_pct, 1)}
          {card.rr_estimate != null ? ` · R:R 1:${card.rr_estimate.toFixed(1)}` : ""}
        </span>
        <span className="text-text-tertiary text-right">${card.last_close.toFixed(2)}</span>
      </div>
      {/* Module mini-bars — 5 signed dots */}
      <div className="mt-1 flex gap-0.5">
        {card.modules.map((m) => {
          const pct = Math.min(100, Math.abs(m.score));
          const color =
            m.score > 0 ? "bg-green" : m.score < 0 ? "bg-red" : "bg-text-tertiary/40";
          return (
            <div
              key={m.name}
              title={`${m.name}: ${m.score.toFixed(0)}`}
              className="flex-1 h-1 bg-bg-elevated rounded overflow-hidden"
            >
              <div className={`h-1 ${color}`} style={{ width: `${pct}%` }} />
            </div>
          );
        })}
      </div>
    </article>
  );
}

/**
 * Fast indicator-only multi-asset scanner panel. Pulls /api/v1/scanner/fast
 * every 60s. Renders bullish/bearish columns with tier-coloured cards.
 *
 * Sits above the legacy ML-driven scanner radar in Tab3Scanner — the two
 * are complementary: this one surveys 200 coins on indicator math; the
 * legacy radar surfaces ML predictions for symbols the live worker has
 * actively scored.
 */
export function FastScanRadar({
  timeframe = "1h",
  refreshIntervalMs = 60_000,
}: Props) {
  const [data, setData] = useState<FastScanRadarData | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [tier, setTier] = useState<string>("");

  function openLivePrediction(symbol: string): void {
    window.location.hash = buildLivePredictionHash(symbol, timeframe);
  }

  useEffect(() => {
    let cancelled = false;
    const fetchOnce = async () => {
      try {
        const args: { timeframe: string; tier?: string } = { timeframe };
        if (tier) args.tier = tier;
        const out = await api.scannerFast(args);
        if (!cancelled) {
          setData(out);
          setErr(null);
        }
      } catch (e: unknown) {
        if (!cancelled) setErr((e as Error).message);
      }
    };
    void fetchOnce();
    const t = setInterval(() => void fetchOnce(), refreshIntervalMs);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [timeframe, tier, refreshIntervalMs]);

  const TIER_FILTERS = [
    { id: "", label: "All" },
    { id: "confirmed", label: "Confirmed" },
    { id: "probable", label: "Probable" },
    { id: "weak", label: "Weak" },
    { id: "diverging", label: "Diverging" },
  ];

  if (err) {
    return (
      <section
        className="bg-bg-panel rounded border border-red/40 p-2 mb-2"
        aria-label="fast scan radar"
      >
        <div className="text-[9px] text-red font-mono">
          ⚠ fast-scan error: {err}
        </div>
      </section>
    );
  }

  return (
    <section
      className="bg-bg-panel rounded border border-border p-2 mb-2"
      aria-label="fast scan radar"
    >
      <header className="flex items-center justify-between mb-2 flex-wrap gap-2">
        <div className="flex items-center gap-2">
          <h2 className="text-[10px] uppercase tracking-wide text-text-secondary font-semibold">
            Fast Scan
          </h2>
          <span className="text-[9px] text-text-tertiary">
            {timeframe} · indicator-only · {data?.cache_size ?? 0} symbols
          </span>
        </div>
        <div className="flex gap-1">
          {TIER_FILTERS.map((f) => (
            <button
              key={f.id || "all"}
              type="button"
              onClick={() => setTier(f.id)}
              className={`text-[9px] px-2 py-0.5 rounded border transition-colors ${
                tier === f.id
                  ? "bg-text-tertiary/20 border-text-tertiary text-text-primary"
                  : "border-border text-text-tertiary hover:border-text-tertiary/60"
              }`}
            >
              {f.label}
              {data?.by_tier[f.id] != null ? ` (${data.by_tier[f.id]})` : ""}
            </button>
          ))}
        </div>
      </header>

      {data == null ? (
        <div className="text-[9px] text-text-tertiary font-mono">
          loading scan results…
        </div>
      ) : data.cache_size === 0 ? (
        <div className="text-[9px] text-text-tertiary font-mono">
          scanner warming up — first batch in &lt; 60s
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
          <div>
            <div className="text-[9px] text-green font-mono uppercase tracking-wide mb-1">
              ▲ Bullish ({data.bullish.length})
            </div>
            {data.bullish.length === 0 ? (
              <div className="text-[9px] text-text-tertiary">
                no bullish signals
              </div>
            ) : (
              data.bullish.map((c) => (
                <ScanCardRow
                  key={c.symbol}
                  card={c}
                  side="long"
                  onOpen={openLivePrediction}
                />
              ))
            )}
          </div>
          <div>
            <div className="text-[9px] text-red font-mono uppercase tracking-wide mb-1">
              ▼ Bearish ({data.bearish.length})
            </div>
            {data.bearish.length === 0 ? (
              <div className="text-[9px] text-text-tertiary">
                no bearish signals
              </div>
            ) : (
              data.bearish.map((c) => (
                <ScanCardRow
                  key={c.symbol}
                  card={c}
                  side="short"
                  onOpen={openLivePrediction}
                />
              ))
            )}
          </div>
        </div>
      )}
    </section>
  );
}
