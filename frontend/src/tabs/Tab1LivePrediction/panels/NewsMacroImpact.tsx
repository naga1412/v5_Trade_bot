import { Panel } from "@/components/ui/Panel";
import type { LivePrediction } from "@/lib/api";

// Panel #17 — News & macro event impact.
// SP-9 Phase F4: now reads `data.news` (populated by the predictor when a
// session is in flight + at least one news_items row matches the symbol
// in the last 60 minutes). When the field is absent / null we keep the
// original "no events" placeholder so legacy payloads stay harmless.

const HEADLINE_MAX_CHARS = 60;

function impactClass(impact: "LOW" | "MEDIUM" | "HIGH"): string {
  if (impact === "HIGH") return "bg-red-500/20 text-red-400";
  if (impact === "MEDIUM") return "bg-orange-500/20 text-orange-400";
  return "bg-gray-500/20 text-text-secondary";
}

function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n) + "…" : s;
}

export function NewsMacroImpact({ data }: { data: LivePrediction | null }) {
  const n = data?.news ?? null;
  if (n === null) {
    return (
      <Panel title="News & Macro">
        <span className="text-text-tertiary">no events</span>
      </Panel>
    );
  }
  const borderClass = n.impact === "HIGH" ? "border-red-500 border" : "";
  return (
    <Panel title="News & Macro" className={borderClass}>
      <div className="flex flex-col gap-1">
        <span className="text-sm">
          {n.top_headline ? truncate(n.top_headline, HEADLINE_MAX_CHARS) : "—"}
        </span>
        <div className="flex items-center justify-between text-xs">
          <span className={`px-1 rounded ${impactClass(n.impact)}`}>
            {n.impact}
          </span>
          <span className="text-text-tertiary">
            {n.recent_count} in last hour
          </span>
        </div>
      </div>
    </Panel>
  );
}
