/**
 * SP-8 Phase I — kill switches panel.
 *
 * Each switch has enable/disable toggle + threshold editor. Disabling
 * triggers hardware-confirm modal (Phase J wiring). Tripped switches
 * show red.
 */
import { Panel } from "@/components/ui/Panel";

interface SwitchRow {
  name: string;
  enabled: boolean;
  threshold: string;
  tripped: boolean;
}

// Phase J populates from GET /api/v1/me/kill-switches.
const SWITCHES: SwitchRow[] = [
  { name: "Daily loss",          enabled: true, threshold: "min(2% port, $200)", tripped: false },
  { name: "Consecutive losses",  enabled: true, threshold: "5 in a row",         tripped: false },
  { name: "Network outage",      enabled: true, threshold: "60s",                tripped: false },
  { name: "Slippage",            enabled: true, threshold: "0.5%",               tripped: false },
  { name: "Liquidation near",    enabled: true, threshold: "<50% buffer",        tripped: false },
  { name: "Funding rate guard",  enabled: true, threshold: "±1%/day",            tripped: false },
];

export function KillSwitches() {
  return (
    <Panel title="Kill Switches">
      <table className="w-full text-[8.5px]">
        <thead>
          <tr className="text-text-tertiary text-left">
            <th className="font-normal pb-1">Switch</th>
            <th className="font-normal pb-1 text-right">Threshold</th>
            <th className="font-normal pb-1 text-right">State</th>
          </tr>
        </thead>
        <tbody>
          {SWITCHES.map((s) => (
            <tr key={s.name}>
              <td className="text-text-secondary">{s.name}</td>
              <td className="text-right text-text-secondary">{s.threshold}</td>
              <td className="text-right">
                {s.tripped ? (
                  <span className="text-red">TRIPPED</span>
                ) : s.enabled ? (
                  <span className="text-green">armed</span>
                ) : (
                  <span className="text-text-tertiary">disabled</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-[8px] text-text-tertiary mt-1">
        Disabling any switch requires hardware-confirm. Tripped switches block new positions until reset.
      </p>
    </Panel>
  );
}
