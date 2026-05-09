/**
 * SP-8 Phase I — Autonomous Trading tab.
 *
 * Spec §12.2. Composes the panels:
 *   - ModeSwitcher (top)
 *   - GateStatus
 *   - LivePositions
 *   - KillSwitches
 *   - RecentActivity
 *   - TaxExport
 *   - Settings (sub-section)
 *
 * Backend wiring (REST endpoints + WebSocket subscriptions for live
 * positions, kill switch state, mode change, tax export download)
 * lands in Phase J. For Phase I the panels render their layout with
 * a "data not yet wired" empty state — the structural scaffolding
 * users see when they navigate to the tab even before live trading
 * is enabled.
 */
import { ModeSwitcher } from "./panels/ModeSwitcher";
import { GateStatus } from "./panels/GateStatus";
import { LivePositions } from "./panels/LivePositions";
import { KillSwitches } from "./panels/KillSwitches";
import { RecentActivity } from "./panels/RecentActivity";
import { TaxExport } from "./panels/TaxExport";
import { AutonomousSettings } from "./panels/AutonomousSettings";

export function Autonomous() {
  return (
    <div className="h-full overflow-y-auto bg-bg-base p-2">
      <div className="max-w-6xl mx-auto space-y-2">
        <ModeSwitcher />
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
          <GateStatus />
          <LivePositions />
          <KillSwitches />
          <RecentActivity />
          <TaxExport />
          <AutonomousSettings />
        </div>
      </div>
    </div>
  );
}
