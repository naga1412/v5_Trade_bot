import { Panel } from "@/components/ui/Panel";
import type { LivePrediction } from "@/lib/api";

// Panel #13 — Fear & Greed index + sentiment placeholder.
// Backend doesn't ship the F&G index adapter or sentiment scoring yet
// (deferred to SP-9 sentiment/macro work). The slot is reserved so the
// MASTER_PLAN §9 layout matches immediately.
// `data` is accepted to keep the panel's call signature uniform with
// siblings; intentionally unused.
// eslint-disable-next-line @typescript-eslint/no-unused-vars
export function SentimentFearGreed(_props: { data: LivePrediction | null }) {
  return (
    <Panel title="Sentiment / F&G">
      <div className="grid grid-cols-2 gap-x-2 gap-y-1">
        <span className="text-text-secondary">Fear &amp; Greed</span>
        <span className="text-right text-text-tertiary">no data</span>
        <span className="text-text-secondary">Sentiment</span>
        <span className="text-right text-text-tertiary">no data</span>
      </div>
    </Panel>
  );
}
