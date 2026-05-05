// SP-6 Phase D6: Tab 3 Scanner Radar — filter pill row.
//
// 7 pills per MASTER_PLAN §9 line 316:
//   All | ✓ Confirmed | ~ Probable | ✗ Weak | ⚡ Diverging | 🛡 Hybrid | ⏱ Analyzing
//
// Counts come from `ScannerFilterCounts` (subset: all/confirmed/probable/weak/
// diverging — backend doesn't yet expose hybrid/analyzing counts so those pills
// render label-only). The active pill toggles via `data-active="true"` so tests
// can assert visual state without depending on tailwind class names.

import type { ScannerFilterCounts } from "@/lib/api";

export type ScannerFilter =
  | "all"
  | "confirmed"
  | "probable"
  | "weak"
  | "diverging"
  | "hybrid"
  | "analyzing";

interface PillSpec {
  id: ScannerFilter;
  label: string;
  activeCls: string;
}

const PILLS: ReadonlyArray<PillSpec> = [
  { id: "all", label: "All", activeCls: "bg-bg-elevated text-text-primary" },
  { id: "confirmed", label: "✓ Confirmed", activeCls: "bg-green/15 text-green border-green/40" },
  { id: "probable", label: "~ Probable", activeCls: "bg-orange/15 text-orange border-orange/40" },
  { id: "weak", label: "✗ Weak", activeCls: "bg-red/15 text-red border-red/40" },
  { id: "diverging", label: "⚡ Diverging", activeCls: "bg-purple/15 text-purple border-purple/40" },
  { id: "hybrid", label: "🛡 Hybrid", activeCls: "bg-orange/15 text-orange border-orange/40" },
  { id: "analyzing", label: "⏱ Analyzing", activeCls: "bg-bg-elevated text-cyan border-border" },
];

function countFor(id: ScannerFilter, counts: ScannerFilterCounts | null): string {
  if (!counts) return "";
  switch (id) {
    case "all": return ` ${counts.all}`;
    case "confirmed": return ` ${counts.confirmed}`;
    case "probable": return ` ${counts.probable}`;
    case "weak": return ` ${counts.weak}`;
    case "diverging": return ` ${counts.diverging}`;
    // hybrid + analyzing aren't part of ScannerFilterCounts yet — render label only.
    case "hybrid":
    case "analyzing":
      return "";
  }
}

interface Props {
  active: ScannerFilter;
  counts: ScannerFilterCounts | null;
  onChange: (f: ScannerFilter) => void;
}

export function FilterPills({ active, counts, onChange }: Props) {
  return (
    <div className="flex flex-wrap gap-1" role="group" aria-label="filter pills">
      {PILLS.map((p) => {
        const isActive = p.id === active;
        const cls = isActive
          ? p.activeCls
          : "bg-bg-base text-text-secondary";
        return (
          <button
            key={p.id}
            type="button"
            onClick={() => onChange(p.id)}
            data-active={isActive ? "true" : "false"}
            className={`h-7 px-2 text-[9px] uppercase tracking-wide border border-border rounded ${cls}`}
          >
            {p.label}{countFor(p.id, counts)}
          </button>
        );
      })}
    </div>
  );
}
