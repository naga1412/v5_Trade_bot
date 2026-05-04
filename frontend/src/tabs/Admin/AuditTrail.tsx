import { useCallback, useEffect, useMemo, useState } from "react";
import { Panel } from "@/components/ui/Panel";
import {
  api,
  type AuditTrailEntry,
  type AuditTrailFilters,
} from "@/lib/api";

// SP-0.7 J5 placeholder for SP-0.7 ship per spec: a flat sortable table over
// the unified /admin/audit-trail endpoint. Filters: user_id / table / since /
// limit. Pagination is offset-based; the spec defers full filter UI to SP-1.

const TABLE_OPTIONS: readonly string[] = [
  "predictions",
  "paper_trades",
  "shadow_trades",
  "auth_violations",
  "impersonation_events",
];

function fmtTs(iso: string): string {
  try {
    return new Date(iso).toISOString().replace("T", " ").slice(0, 19) + "Z";
  } catch {
    return iso;
  }
}

export function AuditTrail() {
  const [userIdInput, setUserIdInput] = useState<string>("");
  const [tableInput, setTableInput] = useState<string>("");
  const [sinceInput, setSinceInput] = useState<string>("");
  const [limit, setLimit] = useState<number>(100);
  const [rows, setRows] = useState<readonly AuditTrailEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const filters = useMemo<AuditTrailFilters>(() => {
    const f: AuditTrailFilters = { limit };
    const uid = Number(userIdInput);
    if (userIdInput !== "" && Number.isFinite(uid)) f.user_id = uid;
    if (tableInput !== "") f.table = tableInput;
    if (sinceInput !== "") f.since = sinceInput;
    return f;
  }, [userIdInput, tableInput, sinceInput, limit]);

  const refetch = useCallback(async (): Promise<void> => {
    try {
      const list = await api.adminAuditTrail(filters);
      setRows(list);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [filters]);

  useEffect(() => {
    void refetch();
  }, [refetch]);

  return (
    <Panel
      title="Audit Trail"
      rightSlot={
        <button
          type="button"
          onClick={() => {
            void refetch();
          }}
          className="min-h-[44px] md:min-h-0 md:h-7 px-2 text-[9px] uppercase tracking-wide bg-bg-elevated hover:bg-bg-base border border-border rounded"
        >
          Refresh
        </button>
      }
    >
      <div className="flex flex-wrap gap-2 mb-3 text-[10px]">
        <label className="flex flex-col">
          <span className="uppercase tracking-wide text-text-tertiary">
            User ID
          </span>
          <input
            type="number"
            value={userIdInput}
            onChange={(e) => setUserIdInput(e.target.value)}
            className="min-h-[44px] md:min-h-0 md:h-7 px-2 bg-bg-base border border-border rounded text-xs font-mono w-24"
          />
        </label>
        <label className="flex flex-col">
          <span className="uppercase tracking-wide text-text-tertiary">
            Table
          </span>
          <select
            value={tableInput}
            onChange={(e) => setTableInput(e.target.value)}
            className="min-h-[44px] md:min-h-0 md:h-7 px-2 bg-bg-base border border-border rounded text-xs font-mono"
          >
            <option value="">(all)</option>
            {TABLE_OPTIONS.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col">
          <span className="uppercase tracking-wide text-text-tertiary">
            Since
          </span>
          <input
            type="datetime-local"
            value={sinceInput}
            onChange={(e) => setSinceInput(e.target.value)}
            className="min-h-[44px] md:min-h-0 md:h-7 px-2 bg-bg-base border border-border rounded text-xs font-mono"
          />
        </label>
        <label className="flex flex-col">
          <span className="uppercase tracking-wide text-text-tertiary">
            Limit
          </span>
          <input
            type="number"
            min={1}
            max={1000}
            value={limit}
            onChange={(e) => setLimit(Number(e.target.value) || 100)}
            className="min-h-[44px] md:min-h-0 md:h-7 px-2 bg-bg-base border border-border rounded text-xs font-mono w-20"
          />
        </label>
      </div>
      {error !== null && (
        <div role="alert" className="text-red text-[10px] mb-2">
          {error}
        </div>
      )}
      {rows === null ? (
        <div className="text-text-tertiary">Loading…</div>
      ) : rows.length === 0 ? (
        <div className="text-text-tertiary">No audit entries.</div>
      ) : (
        <table
          aria-label="Audit trail"
          className="w-full text-[10px] font-mono border-collapse"
        >
          <thead>
            <tr className="text-text-tertiary text-left uppercase tracking-wide">
              <th className="py-1 pr-2">Timestamp</th>
              <th className="py-1 pr-2">Table</th>
              <th className="py-1 pr-2">Row</th>
              <th className="py-1 pr-2">User</th>
              <th className="py-1">Summary</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, idx) => (
              <tr
                key={`${r.table_name}-${r.row_id}-${idx}`}
                data-testid="audit-row"
                className="border-t border-border"
              >
                <td className="py-1 pr-2 text-text-secondary whitespace-nowrap">
                  {fmtTs(r.ts)}
                </td>
                <td className="py-1 pr-2">{r.table_name}</td>
                <td className="py-1 pr-2 text-text-secondary">{r.row_id}</td>
                <td className="py-1 pr-2 text-text-secondary">
                  {r.user_id ?? "—"}
                </td>
                <td className="py-1 break-words">{r.summary}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  );
}
