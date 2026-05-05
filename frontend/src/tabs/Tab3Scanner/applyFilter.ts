// SP-6 Phase D5: shared filter helper for BullishColumn + BearishColumn.
//
// Applies the active scanner filter pill + a free-text search box to a card
// list. Search is a case-insensitive substring match against `symbol` or
// `full_name`. The "diverging", "hybrid", and "analyzing" pill states are
// recognised but currently no-op (the backend doesn't tag cards with these
// labels in v1 — wiring them is SP-6.5).

import type { SignalCard as SignalCardData } from "@/lib/api";
import type { ScannerFilter } from "./FilterPills";

export function applyFilter(
  cards: ReadonlyArray<SignalCardData>,
  filter: ScannerFilter,
  search: string,
): SignalCardData[] {
  let out: SignalCardData[] = cards.slice();
  const q = search.trim().toLowerCase();
  if (q) {
    out = out.filter(
      (c) =>
        c.symbol.toLowerCase().includes(q) ||
        c.full_name.toLowerCase().includes(q),
    );
  }
  if (filter === "confirmed") {
    out = out.filter(
      (c) => c.signal_tier === "STANDARD" || c.signal_tier === "A+",
    );
  } else if (filter === "probable") {
    out = out.filter(
      (c) => c.signal_tier === "PAPER" || c.signal_tier === "SMALL",
    );
  } else if (filter === "weak") {
    out = out.filter((c) => c.signal_tier === "NO_SIGNAL");
  } else if (filter === "hybrid") {
    out = out.filter((c) => c.hybrid_flag !== null);
  }
  // "all", "diverging", "analyzing" → no further filter in v1.
  return out;
}

export function buildLivePredictionHash(symbol: string, tf: string): string {
  return `#/live-prediction?symbol=${encodeURIComponent(symbol)}&tf=${encodeURIComponent(tf)}`;
}
