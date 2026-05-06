import { Panel } from "@/components/ui/Panel";
import type { LivePrediction } from "@/lib/api";

// Panel #13 — Fear & Greed index + L9 news bias.
// SP-9 Phase F3: now reads `data.sentiment` (populated by the predictor
// when a session is in flight). When the field is absent / null we keep
// the original "no data" placeholder so the panel stays harmless on
// legacy payloads (no session, F&G upstream down, no news, etc.).

function fngColorClass(value: number): string {
  // Color tiers per SP-9 plan §F3:
  //   <=25 red, <=45 orange, <=55 gray, >=56 green.
  if (value <= 25) return "text-red-400";
  if (value <= 45) return "text-orange-400";
  if (value <= 55) return "text-text-secondary";
  return "text-green-400";
}

function biasColorClass(
  bias: "Bullish" | "Bearish" | "Neutral",
): string {
  if (bias === "Bullish") return "text-green-400";
  if (bias === "Bearish") return "text-red-400";
  return "text-text-secondary";
}

export function SentimentFearGreed({ data }: { data: LivePrediction | null }) {
  const s = data?.sentiment ?? null;
  return (
    <Panel title="Sentiment / F&G">
      <div className="grid grid-cols-2 gap-x-2 gap-y-1">
        <span className="text-text-secondary">Fear &amp; Greed</span>
        {s ? (
          <span className={`text-right ${fngColorClass(s.fng_value)}`}>
            {s.fng_value} · {s.fng_label}
          </span>
        ) : (
          <span className="text-right text-text-tertiary">no data</span>
        )}
        <span className="text-text-secondary">Sentiment</span>
        {s ? (
          <span className={`text-right ${biasColorClass(s.news_bias)}`}>
            {s.news_bias}
          </span>
        ) : (
          <span className="text-right text-text-tertiary">no data</span>
        )}
      </div>
    </Panel>
  );
}
