/**
 * SP-8 Phase I — Indian tax export (Schedule VDA).
 *
 * Shows current FY summary + monthly TDS breakdown + "Download ITR-3
 * export" button. Phase J wires GET /api/v1/me/tax/summary and
 * GET /api/v1/me/tax/export?fy_year=FY2026-27.
 */
import { Panel } from "@/components/ui/Panel";

export function TaxExport() {
  // Phase J populates these from GET /api/v1/me/tax/summary.
  const fyYear = "FY2026-27";
  const totalTrades = 0;
  const realizedPnlInr = 0;
  const tdsOwedInr = 0;

  return (
    <Panel title="Tax Export (Schedule VDA)">
      <table className="w-full text-[9px]">
        <tbody>
          <tr>
            <td className="text-text-secondary">Financial year</td>
            <td className="text-right">{fyYear}</td>
          </tr>
          <tr>
            <td className="text-text-secondary">Closed trades</td>
            <td className="text-right">{totalTrades}</td>
          </tr>
          <tr>
            <td className="text-text-secondary">Realised P&amp;L</td>
            <td className={`text-right ${realizedPnlInr >= 0 ? "text-green" : "text-red"}`}>
              ₹{realizedPnlInr.toLocaleString("en-IN")}
            </td>
          </tr>
          <tr>
            <td className="text-text-secondary">TDS owed</td>
            <td className="text-right">₹{tdsOwedInr.toLocaleString("en-IN")}</td>
          </tr>
        </tbody>
      </table>
      <button
        type="button"
        disabled
        className="mt-2 w-full px-2 py-1 text-[9px] uppercase tracking-wide rounded border border-border bg-bg-base text-text-tertiary cursor-not-allowed"
      >
        Download ITR-3 CSV (Phase J)
      </button>
      <p className="text-[8px] text-text-tertiary mt-1">
        1% TDS per Section 194S, FIFO cost basis, INR-converted at trade-close USD/INR rate.
      </p>
    </Panel>
  );
}
