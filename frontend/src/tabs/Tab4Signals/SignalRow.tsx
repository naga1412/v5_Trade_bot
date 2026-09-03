// Phase 4: one row in the Signals tab. Full-precision entry/SL/TP (the
// operator retypes these into Binance by hand -- a rounded display
// value is a real trading-error risk per the operator's own
// instruction, not cosmetic). Cohort badge is a distinct color block,
// not a small text label, so it can't be skimmed past.
//
// isNewCohort mirrors Tasks 10/11's condition exactly (operator ruling:
// liquidity_added_spot and futures_poll both carry the same "thin,
// newly-qualified" risk; only established_top20 is exempt). Unlike the
// Telegram card (Task 11), the app view has room to show *which* new
// cohort a row belongs to, so the badge and liquidity-summary text
// distinguish the two rather than collapsing them into one label.

import type { TelegramSignal } from "@/lib/api";

interface Props {
  signal: TelegramSignal;
}

function fmtFullPrecision(n: number): string {
  // No rounding -- show what the API returned, full precision.
  return String(n);
}

const COHORT_BADGE_LABEL: Record<string, string> = {
  liquidity_added_spot: "🆕 New — Spot",
  futures_poll: "🆕 New — Futures",
};

export function SignalRow({ signal: s }: Props) {
  const isNewCohort = s.symbol_source !== "established_top20";
  return (
    <tr className={isNewCohort ? "bg-amber-950/30" : undefined}>
      <td className="px-2 py-1 whitespace-nowrap">
        {new Date(s.sent_at).toLocaleString()}
      </td>
      <td className="px-2 py-1 font-mono">{s.symbol}</td>
      <td className={`px-2 py-1 ${s.direction === "LONG" ? "text-green" : "text-red"}`}>
        {s.direction}
      </td>
      <td className="px-2 py-1 font-mono">{fmtFullPrecision(s.entry_price)}</td>
      <td className="px-2 py-1 font-mono">{fmtFullPrecision(s.stop_loss_price)}</td>
      <td className="px-2 py-1 font-mono">{fmtFullPrecision(s.take_profit_price)}</td>
      <td className="px-2 py-1">{s.rr_ratio.toFixed(2)}</td>
      <td className="px-2 py-1">{s.confidence_pct.toFixed(0)}%</td>
      <td className="px-2 py-1">{s.status ?? "pending"}</td>
      <td className="px-2 py-1">
        {isNewCohort ? (
          <span className="inline-block px-2 py-0.5 rounded bg-amber-600 text-white text-[10px] font-bold uppercase">
            {COHORT_BADGE_LABEL[s.symbol_source] ?? "🆕 New Cohort"}
          </span>
        ) : (
          <span className="text-text-tertiary text-[10px]">established</span>
        )}
      </td>
      <td className="px-2 py-1 text-[10px] text-text-tertiary">
        {isNewCohort && s.qvol_24h != null
          ? `vol $${s.qvol_24h.toLocaleString()} • ${s.spread_bps?.toFixed(1)}bps • depth $${s.depth_0_5pct_usdt?.toLocaleString()}`
          : "—"}
      </td>
    </tr>
  );
}
