/**
 * SP-8 Phase I — Autonomous-Trading settings sub-panel.
 *
 * Position sizing config, leverage cap, quiet hours, vault status,
 * TOTP setup. Phase J wires the mutations through hardware-confirm.
 */
import { Panel } from "@/components/ui/Panel";

export function AutonomousSettings() {
  // Phase J reads these from GET /api/v1/me/settings.
  const positionSizingMode = "fixed";
  const fixedSizeRange = "$30 (default)";
  const maxLeverageCap = 10;
  const quietHours = "23:00 IST – 07:00 IST";
  const vaultUnlocked = false;
  const totpConfigured = false;

  return (
    <Panel title="Settings">
      <table className="w-full text-[9px]">
        <tbody>
          <tr>
            <td className="text-text-secondary">Position sizing</td>
            <td className="text-right">{positionSizingMode} ({fixedSizeRange})</td>
          </tr>
          <tr>
            <td className="text-text-secondary">Max leverage</td>
            <td className="text-right">{maxLeverageCap}×</td>
          </tr>
          <tr>
            <td className="text-text-secondary">Quiet hours</td>
            <td className="text-right">{quietHours}</td>
          </tr>
          <tr>
            <td className="text-text-secondary">API key vault</td>
            <td className={`text-right ${vaultUnlocked ? "text-green" : "text-text-tertiary"}`}>
              {vaultUnlocked ? "unlocked" : "not configured"}
            </td>
          </tr>
          <tr>
            <td className="text-text-secondary">TOTP</td>
            <td className={`text-right ${totpConfigured ? "text-green" : "text-text-tertiary"}`}>
              {totpConfigured ? "configured" : "not set up"}
            </td>
          </tr>
        </tbody>
      </table>
      <p className="text-[8px] text-text-tertiary mt-1">
        Editing requires hardware-confirm (TOTP or Telegram code) per spec §10.1.
      </p>
    </Panel>
  );
}
