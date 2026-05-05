import { Panel } from "@/components/ui/Panel";
import type { LivePrediction } from "@/lib/api";

// Panel #12 — Intermarket correlation (DXY, Gold) placeholder.
// Backend doesn't ship DXY / Gold correlation adapters yet (deferred —
// likely SP-9 sentiment/macro work). The slot is reserved so the layout
// matches MASTER_PLAN §9 immediately.
// `data` is accepted to keep the panel's call signature uniform with
// siblings; intentionally unused.
// eslint-disable-next-line @typescript-eslint/no-unused-vars
export function IntermarketAnalysis(_props: { data: LivePrediction | null }) {
  return (
    <Panel title="Intermarket">
      <div className="grid grid-cols-2 gap-x-2 gap-y-1">
        <span className="text-text-secondary">DXY corr</span>
        <span className="text-right text-text-tertiary">no data</span>
        <span className="text-text-secondary">Gold corr</span>
        <span className="text-right text-text-tertiary">no data</span>
      </div>
    </Panel>
  );
}
