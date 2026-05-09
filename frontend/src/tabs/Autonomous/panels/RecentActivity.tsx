/**
 * SP-8 Phase I — recent activity log.
 *
 * Last 50 telegram_signals (approved / skipped / executed). Phase J
 * reads from GET /api/v1/me/telegram-signals?limit=50 + WS push for
 * new signals.
 */
import { Panel } from "@/components/ui/Panel";

export function RecentActivity() {
  // Phase J: useRecentSignals() hook.
  const events: never[] = [];

  return (
    <Panel title="Recent Activity">
      {events.length === 0 ? (
        <>
          <span className="text-text-tertiary">No recent activity</span>
          <p className="text-[8px] text-text-tertiary mt-1">
            Once Telegram-approve mode is active, every signal shows here with its approval / skip / timeout outcome.
          </p>
        </>
      ) : (
        <span>(Phase J wires the data)</span>
      )}
    </Panel>
  );
}
