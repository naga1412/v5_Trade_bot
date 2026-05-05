import { useCallback, useEffect, useState } from "react";
import { Panel } from "@/components/ui/Panel";
import { api, type TrapEntry } from "@/lib/api";

// SP-6 Phase E3: Admin -> Traps sub-page.
// Table of (~17) traps with severity/side display and per-row enable/disable.
// Mirrors the PatternsAdmin/MlCheckpoints pattern; no pagination needed since
// the trap registry comfortably fits one screen.

function StatusPill({ enabled }: { enabled: boolean }) {
  const cls = enabled
    ? "bg-green/15 text-green border border-green/30"
    : "bg-red/15 text-red border border-red/30";
  return (
    <span
      data-testid="trap-status-pill"
      className={`inline-block px-1.5 py-0.5 rounded text-[8px] uppercase tracking-wide ${cls}`}
    >
      {enabled ? "Enabled" : "Disabled"}
    </span>
  );
}

function SeverityPill({ severity }: { severity: TrapEntry["severity"] }) {
  // Color mapping per SP-6 plan E3:
  // medium -> yellow (mapped to gold token, which is the closest in our palette);
  // high   -> orange; extreme -> red. Tests assert the substring match.
  const cls =
    severity === "extreme"
      ? "bg-red/15 text-red border border-red/30"
      : severity === "high"
        ? "bg-orange/15 text-orange border border-orange/30"
        : "bg-yellow-500/15 text-yellow-500 border border-yellow-500/30";
  return (
    <span
      data-testid="trap-severity-pill"
      className={`inline-block px-1.5 py-0.5 rounded text-[8px] uppercase tracking-wide ${cls}`}
    >
      {severity}
    </span>
  );
}

export function TrapsAdmin() {
  const [items, setItems] = useState<readonly TrapEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const reload = useCallback(async (): Promise<void> => {
    try {
      const list = await api.adminListTraps();
      setItems(list);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const toggle = useCallback(
    async (tid: string, enable: boolean): Promise<void> => {
      setBusyId(tid);
      try {
        let reason: string | undefined;
        if (!enable) {
          reason = window.prompt("Reason to disable?") ?? "";
        }
        await api.adminToggleTrap(tid, enable, reason);
        await reload();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusyId(null);
      }
    },
    [reload],
  );

  return (
    <Panel
      title={`Traps (${items?.length ?? 0})`}
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
        <div className="text-text-tertiary">No traps configured.</div>
      ) : (
        <table
          aria-label="Traps"
          className="w-full text-[10px] font-mono border-collapse"
        >
          <thead>
            <tr className="text-text-tertiary text-left uppercase tracking-wide">
              <th className="py-1 pr-2">Trap ID</th>
              <th className="py-1 pr-2">Severity</th>
              <th className="py-1 pr-2">Side</th>
              <th className="py-1 pr-2">Symbol</th>
              <th className="py-1 pr-2">TF</th>
              <th className="py-1 pr-2">Status</th>
              <th className="py-1 text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {items.map((t) => (
              <tr
                key={`${t.trap_id}-${t.symbol}-${t.timeframe}`}
                data-testid={`trap-row-${t.trap_id}`}
                className="border-t border-border align-top"
              >
                <td className="py-1 pr-2">{t.trap_id}</td>
                <td className="py-1 pr-2">
                  <SeverityPill severity={t.severity} />
                </td>
                <td className="py-1 pr-2 text-text-secondary">{t.side}</td>
                <td className="py-1 pr-2 text-text-secondary">{t.symbol}</td>
                <td className="py-1 pr-2 text-text-secondary">{t.timeframe}</td>
                <td className="py-1 pr-2">
                  <StatusPill enabled={t.enabled} />
                </td>
                <td className="py-1 text-right">
                  {t.enabled ? (
                    <button
                      type="button"
                      disabled={busyId === t.trap_id}
                      onClick={() => void toggle(t.trap_id, false)}
                      className="min-h-[28px] px-2 text-[9px] uppercase tracking-wide bg-bg-elevated hover:bg-red/20 border border-border rounded disabled:opacity-50"
                    >
                      Disable
                    </button>
                  ) : (
                    <button
                      type="button"
                      disabled={busyId === t.trap_id}
                      onClick={() => void toggle(t.trap_id, true)}
                      className="min-h-[28px] px-2 text-[9px] uppercase tracking-wide bg-green/20 hover:bg-green/30 text-green border border-green/40 rounded disabled:opacity-50"
                    >
                      Enable
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  );
}
