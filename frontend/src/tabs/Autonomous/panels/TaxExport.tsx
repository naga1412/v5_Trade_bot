/**
 * SP-8 Phase J — interactive Indian tax export.
 *
 * Loads /me/tax/summary on mount + when the FY changes; the download
 * button hands off to a plain anchor pointing at /me/tax/export so the
 * browser handles streaming + the Content-Disposition filename.
 */
import { useEffect, useMemo, useState } from "react";
import { Panel } from "@/components/ui/Panel";
import { api, type TaxSummary } from "@/lib/api";

function defaultFyYear(now = new Date()): string {
  // Indian FY: 1-Apr to 31-Mar of the next year.
  const y = now.getUTCFullYear();
  const m = now.getUTCMonth(); // 0-indexed; April = 3
  const start = m >= 3 ? y : y - 1;
  const endTwoDigit = String((start + 1) % 100).padStart(2, "0");
  return `FY${start}-${endTwoDigit}`;
}

const FY_OPTIONS: string[] = (() => {
  const cur = defaultFyYear();
  const startYear = Number(cur.slice(2, 6));
  return [0, 1, 2].map((offset) => {
    const s = startYear - offset;
    const e = String((s + 1) % 100).padStart(2, "0");
    return `FY${s}-${e}`;
  });
})();

export function TaxExport() {
  const [fyYear, setFyYear] = useState<string>(defaultFyYear());
  const [summary, setSummary] = useState<TaxSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api.meTaxSummary(fyYear)
      .then((s) => {
        if (!cancelled) setSummary(s);
      })
      .catch((e: unknown) => {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : "load failed");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [fyYear]);

  const downloadHref = useMemo(
    () => api.meTaxExportUrl(fyYear),
    [fyYear],
  );

  const totalTrades = summary?.total_trades ?? 0;
  const realizedPnlInr = summary?.total_realized_pnl_inr ?? 0;
  const tdsOwedInr = summary?.total_tds_inr ?? 0;

  return (
    <Panel title="Tax Export (Schedule VDA)">
      <div className="flex items-center gap-1 mb-2">
        <label
          htmlFor="tax-fy-year"
          className="text-[8px] uppercase text-text-tertiary"
        >
          FY
        </label>
        <select
          id="tax-fy-year"
          value={fyYear}
          onChange={(e) => setFyYear(e.target.value)}
          className="text-[10px] bg-bg-base border border-border rounded px-1 py-0.5"
        >
          {FY_OPTIONS.map((y) => (
            <option key={y} value={y}>{y}</option>
          ))}
        </select>
      </div>
      <table className="w-full text-[9px]">
        <tbody>
          <tr>
            <td className="text-text-secondary">Closed trades</td>
            <td className="text-right">
              {loading ? "…" : totalTrades}
            </td>
          </tr>
          <tr>
            <td className="text-text-secondary">Realised P&amp;L</td>
            <td
              className={`text-right ${realizedPnlInr >= 0 ? "text-green" : "text-red"}`}
            >
              {loading ? "…" : `₹${realizedPnlInr.toLocaleString("en-IN")}`}
            </td>
          </tr>
          <tr>
            <td className="text-text-secondary">TDS owed</td>
            <td className="text-right">
              {loading ? "…" : `₹${tdsOwedInr.toLocaleString("en-IN")}`}
            </td>
          </tr>
        </tbody>
      </table>
      <a
        href={downloadHref}
        download
        className={[
          "block mt-2 w-full text-center px-2 py-1 text-[9px] uppercase tracking-wide rounded",
          "border border-text-primary bg-bg-base text-text-primary hover:bg-bg-panel",
        ].join(" ")}
      >
        Download Schedule-VDA CSV
      </a>
      <p className="text-[8px] text-text-tertiary mt-1">
        1% TDS per Section 194S, FIFO cost basis, INR-converted at trade-close USD/INR rate.
      </p>
      {error !== null ? (
        <p className="text-[10px] text-text-bearish mt-1" role="alert">
          {error}
        </p>
      ) : null}
    </Panel>
  );
}
