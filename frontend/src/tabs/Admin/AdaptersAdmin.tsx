import { Fragment, useCallback, useEffect, useState } from "react";
import { Panel } from "@/components/ui/Panel";
import { api, type AdapterHealth, type SyncResult } from "@/lib/api";

// SP-6 Phase E2: Admin -> Adapters sub-page.
// Table of registered exchange adapters (binance / bybit / yahoo / twelvedata)
// with last health check + manual sync trigger. Sync result is rendered inline
// below the row after a successful POST.

function fmtTs(iso: string | null): string {
  if (iso === null) return "—";
  try {
    return new Date(iso).toISOString().replace("T", " ").slice(0, 19) + "Z";
  } catch {
    return iso;
  }
}

function HealthPill({ healthy }: { healthy: boolean }) {
  const cls = healthy
    ? "bg-green/15 text-green border border-green/30"
    : "bg-red/15 text-red border border-red/30";
  return (
    <span
      data-testid="adapter-health-pill"
      className={`inline-block px-1.5 py-0.5 rounded text-[8px] uppercase tracking-wide ${cls}`}
    >
      {healthy ? "Healthy" : "Unhealthy"}
    </span>
  );
}

export function AdaptersAdmin() {
  const [items, setItems] = useState<readonly AdapterHealth[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyEx, setBusyEx] = useState<string | null>(null);
  const [syncResults, setSyncResults] = useState<
    Readonly<Record<string, SyncResult>>
  >({});

  const reload = useCallback(async (): Promise<void> => {
    try {
      const list = await api.adminListAdapters();
      setItems(list);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const sync = useCallback(
    async (exchange: string): Promise<void> => {
      setBusyEx(exchange);
      try {
        const res = await api.adminSyncAdapter(exchange);
        setSyncResults((prev) => ({ ...prev, [exchange]: res }));
        await reload();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusyEx(null);
      }
    },
    [reload],
  );

  return (
    <Panel
      title="Adapters"
      rightSlot={
        <button
          type="button"
          onClick={() => void reload()}
          className="min-h-[44px] md:min-h-0 md:h-7 px-2 text-[9px] uppercase tracking-wide bg-bg-elevated hover:bg-bg-base border border-border rounded"
        >
          Refresh
        </button>
      }
    >
      {error !== null && (
        <div role="alert" className="text-red text-[10px] mb-2">
          {error}
        </div>
      )}
      {items === null ? (
        <div className="text-text-tertiary">Loading…</div>
      ) : items.length === 0 ? (
        <div className="text-text-tertiary">No adapters registered.</div>
      ) : (
        <table
          aria-label="Adapters"
          className="w-full text-[10px] font-mono border-collapse"
        >
          <thead>
            <tr className="text-text-tertiary text-left uppercase tracking-wide">
              <th className="py-1 pr-2">Exchange</th>
              <th className="py-1 pr-2">Health</th>
              <th className="py-1 pr-2">Latency</th>
              <th className="py-1 pr-2">Quota</th>
              <th className="py-1 pr-2">Last Check</th>
              <th className="py-1 text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {items.map((a) => {
              const result = syncResults[a.exchange];
              return (
                <Fragment key={a.exchange}>
                  <tr
                    data-testid="adapter-row"
                    className="border-t border-border align-top"
                  >
                    <td className="py-1 pr-2">{a.exchange}</td>
                    <td className="py-1 pr-2">
                      <HealthPill healthy={a.is_healthy} />
                    </td>
                    <td className="py-1 pr-2 text-text-secondary">
                      {a.latency_ms === null ? "—" : `${a.latency_ms} ms`}
                    </td>
                    <td className="py-1 pr-2 text-text-secondary">
                      {a.quota_used_pct === null
                        ? "—"
                        : `${a.quota_used_pct.toFixed(1)}%`}
                    </td>
                    <td className="py-1 pr-2 text-text-tertiary whitespace-nowrap">
                      {fmtTs(a.checked_at)}
                    </td>
                    <td className="py-1 text-right">
                      <button
                        type="button"
                        disabled={busyEx === a.exchange}
                        onClick={() => void sync(a.exchange)}
                        className="min-h-[28px] px-2 text-[9px] uppercase tracking-wide bg-bg-elevated hover:bg-bg-base border border-border rounded disabled:opacity-50"
                      >
                        Sync
                      </button>
                    </td>
                  </tr>
                  {result !== undefined && (
                    <tr className="border-t border-border/50">
                      <td
                        colSpan={6}
                        className="py-1 pr-2 text-[9px] text-text-secondary"
                        data-testid={`adapter-sync-result-${a.exchange}`}
                      >
                        Added {result.added}, still {result.still_active},
                        removed {result.newly_delisted}
                      </td>
                    </tr>
                  )}
                  {a.error_message !== null && (
                    <tr className="border-t border-border/50">
                      <td colSpan={6} className="py-1 pr-2 text-[9px] text-red">
                        {a.error_message}
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      )}
    </Panel>
  );
}
