// SP-6 Phase D3: Tab 3 Scanner Radar — cyan progress bar.
//
// Visualises the SP-2 Hybrid Supervisor's "X/8 done" status (eight per-asset
// agents working through a queue). Backend exposes `supervisor_progress` on the
// `/scanner/radar` payload (`{done, total}`); when the field is `null` (cold
// start, no scan run yet) we render `0/total` with no fill.

import type { SupervisorProgress } from "@/lib/api";

interface Props {
  progress: SupervisorProgress | null;
}

export function HybridSupervisorBar({ progress }: Props) {
  const done = progress?.done ?? 0;
  const total = progress?.total ?? 8;
  const denom = Math.max(1, total);
  const pct = Math.min(100, Math.max(0, (done / denom) * 100));
  return (
    <div className="px-2 py-1 border-b border-border bg-bg-base">
      <div className="flex justify-between items-center text-[8px] uppercase tracking-wide text-text-tertiary mb-1">
        <span>Hybrid Supervisor</span>
        <span aria-label="supervisor progress">{done}/{total} done</span>
      </div>
      <div className="h-1 bg-bg-elevated rounded overflow-hidden">
        <div
          className="h-1 bg-cyan rounded"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}
