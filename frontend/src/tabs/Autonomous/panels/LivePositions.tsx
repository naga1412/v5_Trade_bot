/**
 * SP-8 Phase I — live positions panel.
 *
 * Open live_trades with real-time P&L + time-to-liquidation + manual
 * close button. Phase J subscribes to /ws/v1/live_positions and hits
 * POST /api/v1/me/positions/{id}/close on the close button.
 */
import { Panel } from "@/components/ui/Panel";

export function LivePositions() {
  // Phase J: useLivePositions() hook reading the WS feed.
  const positions: never[] = [];

  if (positions.length === 0) {
    return (
      <Panel title="Live Positions">
        <span className="text-text-tertiary">No open live positions</span>
        <p className="text-[8px] text-text-tertiary mt-1">
          Live positions will appear here once Telegram-approve or Fully-auto mode is active and a trade is placed.
        </p>
      </Panel>
    );
  }

  return <Panel title="Live Positions">(unreachable — Phase J wires data)</Panel>;
}
