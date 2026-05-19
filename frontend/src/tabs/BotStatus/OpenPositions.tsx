import { Panel } from "@/components/ui/Panel";
import { useOpenPositionsLive } from "./hooks/useOpenPositionsLive";
import { buildLivePredictionHash } from "@/tabs/Tab3Scanner/applyFilter";
import type { OpenPosition } from "@/lib/api";

function fmtNum(v: number | null | undefined, dp = 2): string {
  if (v == null) return "—";
  return v.toFixed(dp);
}

function fmtPct(v: number | null | undefined, dp = 2): string {
  if (v == null) return "—";
  const sign = v >= 0 ? "+" : "";
  return `${sign}${v.toFixed(dp)}%`;
}

function timeHeld(openedAt: string, now: Date = new Date()): string {
  const opened = new Date(openedAt).getTime();
  const diffMs = now.getTime() - opened;
  if (!isFinite(diffMs) || diffMs < 0) return "—";
  const totalMin = Math.floor(diffMs / 60000);
  const h = Math.floor(totalMin / 60);
  const m = totalMin % 60;
  return `${h}h ${m}m`;
}

function pnlClass(v: number | null | undefined): string {
  if (v == null) return "text-text-secondary";
  return v >= 0 ? "text-green" : "text-red";
}

function PositionCard({ pos }: { pos: OpenPosition }) {
  const arrow = pos.direction === "LONG" ? "↗" : "↘";
  const dirClass = pos.direction === "LONG" ? "text-green" : "text-red";
  const open = (): void => {
    // PR3 Phase 8: read per-position timeframe so 15m positions deep-link
    // to the 15m chart and 1h positions to the 1h chart. Fallback "1h"
    // covers pre-PR3 positions that haven't been migrated to carry the
    // field yet (API field is Optional for forward-compat).
    window.location.hash = buildLivePredictionHash(
      pos.symbol, pos.timeframe ?? "1h",
    );
  };
  return (
    <div
      className="border border-border rounded p-1 bg-bg-elevated min-w-[140px] cursor-pointer hover:border-text-tertiary/40 focus:outline-none focus:border-text-tertiary transition-colors"
      role="button"
      tabIndex={0}
      aria-label={`open ${pos.symbol} ${pos.direction} chart`}
      onClick={open}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          open();
        }
      }}
    >
      <div className="flex items-center justify-between mb-1">
        <span className="font-bold">{pos.symbol}</span>
        <span className={dirClass}>{arrow}</span>
      </div>
      <div className="grid grid-cols-2 gap-x-1 gap-y-0.5">
        <span className="text-text-tertiary">Entry</span>
        <span className="text-right">{fmtNum(pos.entry_price)}</span>
        <span className="text-text-tertiary">SL</span>
        <span className="text-right text-red">{fmtNum(pos.stop_loss)}</span>
        <span className="text-text-tertiary">TP</span>
        <span className="text-right text-green">{fmtNum(pos.take_profit)}</span>
        <span className="text-text-tertiary">Now</span>
        <span className="text-right">{fmtNum(pos.current_price)}</span>
        <span className="text-text-tertiary">P&L</span>
        <span className={`text-right ${pnlClass(pos.unrealized_pnl_pct)}`}>
          {fmtPct(pos.unrealized_pnl_pct)}
        </span>
      </div>
      <div className="mt-1 text-[8px] text-text-tertiary text-right">
        held {timeHeld(pos.opened_at)}
      </div>
    </div>
  );
}

export function OpenPositions() {
  const { data, error, loading } = useOpenPositionsLive();

  if (error) {
    return (
      <Panel title="Open Positions" intensity="alert">
        <span className="text-red">Error: {error}</span>
      </Panel>
    );
  }

  if (loading || data == null) {
    return (
      <Panel title="Open Positions">
        <span>—</span>
      </Panel>
    );
  }

  if (data.length === 0) {
    return (
      <Panel title="Open Positions">
        <span className="text-text-secondary">No open positions</span>
      </Panel>
    );
  }

  return (
    <Panel title="Open Positions">
      <div className="flex flex-col md:flex-row gap-1 md:overflow-x-auto">
        {data.map((p) => (
          <PositionCard key={p.signal_id} pos={p} />
        ))}
      </div>
      {/* T-UI.3 / PR10.5: surface server clock so operators can spot a
          stalled poll/WS feed. Guarded on `as_of` presence so we don't
          crash during the backend rollout window (older responses
          might lack the field). */}
      {data[0]?.as_of && (
        <div className="mt-1 text-[8px] text-text-tertiary text-right">
          as of {new Date(data[0].as_of).toLocaleTimeString()}
        </div>
      )}
    </Panel>
  );
}
