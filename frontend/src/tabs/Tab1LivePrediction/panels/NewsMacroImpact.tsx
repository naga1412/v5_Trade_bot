import { Panel } from "@/components/ui/Panel";
import type { LivePrediction } from "@/lib/api";

// Panel #17 — News & macro event impact placeholder.
// Backend doesn't ship a news / macro-event calendar adapter yet
// (deferred — likely SP-9 sentiment/macro work). The slot is reserved so
// the MASTER_PLAN §9 layout matches immediately.
// `data` is accepted to keep the panel's call signature uniform with
// siblings; intentionally unused.
// eslint-disable-next-line @typescript-eslint/no-unused-vars
export function NewsMacroImpact(_props: { data: LivePrediction | null }) {
  return (
    <Panel title="News & Macro">
      <span className="text-text-tertiary">no events</span>
    </Panel>
  );
}
