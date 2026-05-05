import { Panel } from "@/components/ui/Panel";
import type { LivePrediction } from "@/lib/api";

// Backend volume layer (L5) reports POC/VAH/VAL inside its free-text notes
// field for SP-6 — structured fields are deferred. Parse the simple
// "POC=N VAH=N VAL=N" pattern; fall back to em-dashes when absent.
function parsePoc(notes: string): { poc: string; vah: string; val: string } {
  const m = notes.match(/POC=([0-9.]+).*VAH=([0-9.]+).*VAL=([0-9.]+)/);
  if (!m) return { poc: "—", vah: "—", val: "—" };
  return { poc: m[1] ?? "—", vah: m[2] ?? "—", val: m[3] ?? "—" };
}

// Panel #7 — POC / VAH / VAL from L5 notes.
export function VolumeProfile({ data }: { data: LivePrediction | null }) {
  if (!data) return <Panel title="Volume Profile">—</Panel>;
  const l5 = data.layer_scores?.["5"];
  const { poc, vah, val } = parsePoc(l5?.notes ?? "");
  return (
    <Panel title="Volume Profile">
      <div className="grid grid-cols-2 gap-x-2 gap-y-1">
        <span className="text-text-secondary">POC</span>
        <span className="text-right">{poc}</span>
        <span className="text-text-secondary">VAH</span>
        <span className="text-right text-green">{vah}</span>
        <span className="text-text-secondary">VAL</span>
        <span className="text-right text-red">{val}</span>
      </div>
    </Panel>
  );
}
