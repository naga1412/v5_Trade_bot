import { Panel } from "@/components/ui/Panel";
import type { LivePrediction } from "@/lib/api";

// Panel #11 — Open Interest + funding rate placeholder.
// Backend doesn't ship OI / funding adapters until SP-3.5 (or SP-7);
// per spec §3.2 the slot is reserved with permanent "no data" until then.
export function OiFundingRate({ data: _data }: { data: LivePrediction | null }) {
  return (
    <Panel title="OI & Funding Rate">
      <div className="grid grid-cols-2 gap-x-2 gap-y-1">
        <span className="text-text-secondary">OI delta</span>
        <span className="text-right text-text-tertiary">no data</span>
        <span className="text-text-secondary">Funding</span>
        <span className="text-right text-text-tertiary">no data</span>
      </div>
    </Panel>
  );
}
