import { Panel } from "@/components/ui/Panel";
import { useOpenPositionsLive } from "./hooks/useOpenPositionsLive";
import { buildLivePredictionHash } from "@/tabs/Tab3Scanner/applyFilter";
import type { OpenPosition } from "@/lib/api";

// PR10.8: mirror of backend Settings.SHADOW_SPOT_BLACKLIST. These symbols
// fail Binance SPOT /api/v3/ticker/price (futures-only, delisted, restricted),
// so the cold-load `current_price` is null for them by design. WS-tick
// updates may still arrive if SPOT klines + websocket work for the symbol
// (e.g. UUSDT). The hardcoded list is a UX hint — kept short + reviewed
// alongside the backend Setting; drift is acceptable since this only
// affects tooltip wording, not behavior.
const SHADOW_SPOT_BLACKLIST: ReadonlySet<string> = new Set([
  "EDENUSDT", "LUNCUSDT", "PAXGUSDT", "XAUTUSDT", "UUSDT",
]);

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
  // PR10.8: when symbol is in SHADOW_SPOT_BLACKLIST AND current_price is null,
  // surface a more specific tooltip than the generic "Spot price unavailable".
  const isBlacklisted = SHADOW_SPOT_BLACKLIST.has(pos.symbol);
  const blacklistedAndEmpty =
    isBlacklisted && pos.current_price == null;
  const nowTooltip =
    pos.current_price == null
      ? blacklistedAndEmpty
        ? `Not priceable on Binance SPOT (futures-only / delisted / restricted) — ${pos.symbol}`
        : `Spot price unavailable for ${pos.symbol}`
      : undefined;
  const pnlTooltip =
    pos.unrealized_pnl_pct == null
      ? blacklistedAndEmpty
        ? `P&L unavailable — ${pos.symbol} is not priceable on Binance SPOT`
        : `P&L unavailable — spot price missing for ${pos.symbol}`
      : undefined;
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
        <span className="text-right" title={nowTooltip}>
          {fmtNum(pos.current_price)}
          {blacklistedAndEmpty && (
            <span
              className="ml-0.5 text-[8px] text-text-tertiary"
              aria-label="blacklisted-info"
            >
              ⓘ
            </span>
          )}
        </span>
        <span className="text-text-tertiary">P&L</span>
        <span
          className={`text-right ${pnlClass(pos.unrealized_pnl_pct)}`}
          title={pnlTooltip}
        >
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
