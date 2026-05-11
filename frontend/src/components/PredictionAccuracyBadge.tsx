import { useEffect, useState } from "react";
import { api, type PredictionAccuracy } from "@/lib/api";

interface Props {
  symbolPath: string;     // "BTC-USDT" (URL-safe)
  timeframe: string;
  refreshIntervalMs?: number;  // default 60s
}

/**
 * Small badge showing the rolling accuracy of recent live predictions for
 * the current symbol/timeframe. Honest version of SMC Pro's "127× corrected"
 * counter — we don't retrain, we just measure and surface.
 *
 * Polls the backend every 60s by default. Hidden until we have at least
 * one validated row (otherwise it just shows "—").
 */
export function PredictionAccuracyBadge({
  symbolPath, timeframe, refreshIntervalMs = 60_000,
}: Props) {
  const [data, setData] = useState<PredictionAccuracy | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const fetchOnce = async () => {
      try {
        const out = await api.predictionAccuracy(symbolPath, timeframe);
        if (!cancelled) setData(out);
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
  }, [symbolPath, timeframe, refreshIntervalMs]);

  if (err !== null) {
    return (
      <div
        className="text-[9px] text-text-tertiary font-mono"
        aria-label="prediction accuracy"
      >
        accuracy: err
      </div>
    );
  }
  if (data === null || data.validated_count === 0) {
    return (
      <div
        className="text-[9px] text-text-tertiary font-mono"
        aria-label="prediction accuracy"
      >
        accuracy: warming up ({data?.pending_count ?? 0} pending)
      </div>
    );
  }

  const pct = data.accuracy_pct;
  // ≥55% = bot is calibrated above random; <45% = inverted (worse than coin flip)
  const color =
    pct >= 55 ? "text-green"
      : pct < 45 ? "text-red"
        : "text-text-secondary";
  return (
    <div
      className="text-[9px] font-mono flex items-center gap-2"
      aria-label="prediction accuracy"
    >
      <span className="text-text-tertiary uppercase tracking-wide">
        Live accuracy
      </span>
      <span className={color}>
        {pct.toFixed(0)}% ({data.correct_count}/{data.validated_count})
      </span>
      {data.pending_count > 0 ? (
        <span className="text-text-tertiary">
          · {data.pending_count} pending
        </span>
      ) : null}
    </div>
  );
}
