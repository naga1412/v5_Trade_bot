// SP-6 Phase D5: Tab 3 Scanner Radar — green-titled bullish list.
//
// Renders a green column header with the visible card count and a vertical
// stack of SignalCard. Search + filter come from Tab3Scanner toolbar state.
// Clicking a card navigates to Tab 1 with `?symbol=…&tf=…` query (D8).

import { SignalCard } from "./SignalCard";
import { applyFilter, buildLivePredictionHash } from "./applyFilter";
import type { ScannerFilter } from "./FilterPills";
import type { SignalCard as SignalCardData } from "@/lib/api";

interface Props {
  cards: ReadonlyArray<SignalCardData>;
  search: string;
  filter: ScannerFilter;
  tf: string;
}

export function BullishColumn({ cards, search, filter, tf }: Props) {
  const visible = applyFilter(cards, filter, search);

  function open(symbol: string): void {
    window.location.hash = buildLivePredictionHash(symbol, tf);
  }

  return (
    <div className="flex flex-col min-w-0">
      <h2 className="text-green text-[11px] uppercase tracking-wide mb-1">
        Bullish ({visible.length})
      </h2>
      {visible.length === 0 ? (
        <div className="text-text-tertiary text-[10px]">No signals match</div>
      ) : (
        visible.map((c) => (
          <SignalCard
            key={c.symbol}
            card={c}
            onClick={(card) => open(card.symbol)}
          />
        ))
      )}
    </div>
  );
}
