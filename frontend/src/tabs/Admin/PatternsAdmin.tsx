import { useCallback, useEffect, useMemo, useState } from "react";
import { Panel } from "@/components/ui/Panel";
import { api, type PatternEntry } from "@/lib/api";

// SP-6 Phase E1: Admin -> Patterns sub-page.
// Table of (up to 158) patterns with per-row enable/disable toggle, name
// search, and pagination (50/page). Mirrors the SP-1 MlCheckpoints style.

const PAGE_SIZE = 50;

function StatusPill({ enabled }: { enabled: boolean }) {
  const cls = enabled
    ? "bg-green/15 text-green border border-green/30"
    : "bg-red/15 text-red border border-red/30";
  return (
    <span
      data-testid="pattern-status-pill"
      className={`inline-block px-1.5 py-0.5 rounded text-[8px] uppercase tracking-wide ${cls}`}
    >
      {enabled ? "Enabled" : "Disabled"}
    </span>
  );
}

export function PatternsAdmin() {
  const [items, setItems] = useState<readonly PatternEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [search, setSearch] = useState<string>("");
  const [page, setPage] = useState<number>(0);

  const reload = useCallback(async (): Promise<void> => {
    try {
      const list = await api.adminListPatterns();
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
    async (pid: string, enable: boolean): Promise<void> => {
      setBusyId(pid);
      try {
        let reason: string | undefined;
        if (!enable) {
          reason = window.prompt("Reason to disable?") ?? "";
        }
        await api.adminTogglePattern(pid, enable, reason);
        await reload();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusyId(null);
      }
    },
    [reload],
  );

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (q === "") return items ?? [];
    return (items ?? []).filter((p) =>
      p.pattern_id.toLowerCase().includes(q),
    );
  }, [items, search]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage = Math.min(page, totalPages - 1);
  const pageItems = filtered.slice(
    safePage * PAGE_SIZE,
    (safePage + 1) * PAGE_SIZE,
  );

  return (
    <Panel
      title={`Patterns (${items?.length ?? 0})`}
      rightSlot={
        <input
          type="text"
          placeholder="Search…"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setPage(0);
          }}
          aria-label="search patterns"
          className="w-[140px] min-h-[44px] md:min-h-0 md:h-7 px-2 text-[10px] bg-bg-base border border-border rounded font-mono"
        />
      }
    >
      {error !== null && (
        <div role="alert" className="text-red text-[10px] mb-2">
          {error}
        </div>
      )}
      {items === null ? (
        <div className="text-text-tertiary">Loading…</div>
      ) : filtered.length === 0 ? (
        <div className="text-text-tertiary">No patterns match.</div>
      ) : (
        <>
          <table
            aria-label="Patterns"
            className="w-full text-[10px] font-mono border-collapse"
          >
            <thead>
              <tr className="text-text-tertiary text-left uppercase tracking-wide">
                <th className="py-1 pr-2">Pattern ID</th>
                <th className="py-1 pr-2">Type</th>
                <th className="py-1 pr-2">Symbol</th>
                <th className="py-1 pr-2">TF</th>
                <th className="py-1 pr-2">Status</th>
                <th className="py-1 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {pageItems.map((p) => (
                <tr
                  key={`${p.pattern_id}-${p.symbol}-${p.timeframe}`}
                  data-testid={`pattern-row-${p.pattern_id}`}
                  className="border-t border-border align-top"
                >
                  <td className="py-1 pr-2">{p.pattern_id}</td>
                  <td className="py-1 pr-2 text-text-secondary">
                    {p.pattern_type}
                  </td>
                  <td className="py-1 pr-2 text-text-secondary">{p.symbol}</td>
                  <td className="py-1 pr-2 text-text-secondary">
                    {p.timeframe}
                  </td>
                  <td className="py-1 pr-2">
                    <StatusPill enabled={p.enabled} />
                  </td>
                  <td className="py-1 text-right">
                    {p.enabled ? (
                      <button
                        type="button"
                        disabled={busyId === p.pattern_id}
                        onClick={() => void toggle(p.pattern_id, false)}
                        className="min-h-[28px] px-2 text-[9px] uppercase tracking-wide bg-bg-elevated hover:bg-red/20 border border-border rounded disabled:opacity-50"
                      >
                        Disable
                      </button>
                    ) : (
                      <button
                        type="button"
                        disabled={busyId === p.pattern_id}
                        onClick={() => void toggle(p.pattern_id, true)}
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
          {totalPages > 1 && (
            <div className="flex justify-center items-center gap-2 mt-2">
              <button
                type="button"
                disabled={safePage === 0}
                onClick={() => setPage(safePage - 1)}
                className="min-h-[28px] px-2 text-[10px] bg-bg-elevated border border-border rounded disabled:opacity-50"
              >
                ‹ Prev
              </button>
              <span className="text-[10px] text-text-tertiary">
                Page {safePage + 1} / {totalPages}
              </span>
              <button
                type="button"
                disabled={safePage + 1 >= totalPages}
                onClick={() => setPage(safePage + 1)}
                className="min-h-[28px] px-2 text-[10px] bg-bg-elevated border border-border rounded disabled:opacity-50"
              >
                Next ›
              </button>
            </div>
          )}
        </>
      )}
    </Panel>
  );
}
