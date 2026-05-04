// SP-0.5 Phase N: Bot Status tab assembled from 7 sections.
//
// Refresh strategy: each section's hook performs a single REST fetch on mount
// and provides a manual refresh button (⟳) on the panel header. WS-reactive
// hooks (OverviewStats, OpenPositions) additionally refetch when relevant
// shadow_updates events arrive (closed → overview; opened/closed → positions).
// We deliberately avoid a global polling loop to minimise REST traffic; the
// shadow_updates WebSocket is the source of freshness signals.

import { OverviewStats } from "./OverviewStats";
import { PromotionGate } from "./PromotionGate";
import { OpenPositions } from "./OpenPositions";
import { PerAssetTable } from "./PerAssetTable";
import { LongShortBreakdown } from "./LongShortBreakdown";
import { EquityCurve } from "./EquityCurve";
import { RecentTrades } from "./RecentTrades";

export function BotStatus() {
  return (
    <div className="h-full overflow-y-auto p-2 bg-bg-base">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-2 mb-2">
        <OverviewStats />
        <PromotionGate />
      </div>
      <OpenPositions />
      <PerAssetTable />
      <LongShortBreakdown />
      <EquityCurve />
      <RecentTrades />
    </div>
  );
}
