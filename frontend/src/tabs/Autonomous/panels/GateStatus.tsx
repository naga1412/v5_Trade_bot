/**
 * SP-8 Phase I — promotion gate status.
 *
 * Shows current rolling-window stats vs the per-mode thresholds from
 * spec §4. Phase J wires this to GET /api/v1/me/promotion-gates.
 */
import { Panel } from "@/components/ui/Panel";

interface GateRow {
  metric: string;
  current: string;
  telegramReq: string;
  fullyAutoReq: string;
  passingTelegram: boolean;
  passingFullyAuto: boolean;
}

// Phase J fills these from the API. For now: empty-state placeholders
// so the shape is visible.
const ROWS: GateRow[] = [
  { metric: "Days continuous",        current: "—", telegramReq: "≥30",   fullyAutoReq: "≥90",   passingTelegram: false, passingFullyAuto: false },
  { metric: "Closed trades",          current: "—", telegramReq: "≥100",  fullyAutoReq: "≥300",  passingTelegram: false, passingFullyAuto: false },
  { metric: "Sharpe (annualised)",    current: "—", telegramReq: "≥1.0",  fullyAutoReq: "≥1.5",  passingTelegram: false, passingFullyAuto: false },
  { metric: "Max drawdown",           current: "—", telegramReq: "≤12%",  fullyAutoReq: "≤10%",  passingTelegram: false, passingFullyAuto: false },
  { metric: "Win rate",               current: "—", telegramReq: "≥40%",  fullyAutoReq: "≥45%",  passingTelegram: false, passingFullyAuto: false },
  { metric: "Profit factor",          current: "—", telegramReq: "≥1.5",  fullyAutoReq: "≥2.0",  passingTelegram: false, passingFullyAuto: false },
];

export function GateStatus() {
  return (
    <Panel title="Promotion Gates">
      <table className="w-full text-[8.5px]">
        <thead>
          <tr className="text-text-tertiary text-left">
            <th className="font-normal pb-1">Metric</th>
            <th className="font-normal pb-1 text-right">Current</th>
            <th className="font-normal pb-1 text-right">Telegram</th>
            <th className="font-normal pb-1 text-right">Fully-auto</th>
          </tr>
        </thead>
        <tbody>
          {ROWS.map((r) => (
            <tr key={r.metric}>
              <td className="text-text-secondary">{r.metric}</td>
              <td className="text-right">{r.current}</td>
              <td className={`text-right ${r.passingTelegram ? "text-green" : "text-text-tertiary"}`}>
                {r.telegramReq}
              </td>
              <td className={`text-right ${r.passingFullyAuto ? "text-green" : "text-text-tertiary"}`}>
                {r.fullyAutoReq}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-[8px] text-text-tertiary mt-1">
        Stats over rolling 30 day (Telegram) / 90 day (Fully-auto) window of closed shadow + live trades.
      </p>
    </Panel>
  );
}
