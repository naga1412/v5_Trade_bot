import { useCallback, useEffect, useState } from "react";
import { Panel } from "@/components/ui/Panel";
import { api, type MlCheckpoint } from "@/lib/api";

// SP-1 Phase F (task F3): Admin -> ML Checkpoints sub-page.
// Columns: Version | Model | Trained At | Status | Eval Results | Actions.
// Matches the SP-0.7 Admin/Users sub-page pattern.

function fmtTs(iso: string | null): string {
  if (iso === null) return "—";
  try {
    return new Date(iso).toISOString().replace("T", " ").slice(0, 16) + "Z";
  } catch {
    return iso;
  }
}

function shortSha(sha: string): string {
  return sha.length > 12 ? sha.slice(0, 12) + "…" : sha;
}

function StatusPill({ active }: { active: boolean }) {
  const cls = active
    ? "bg-green/15 text-green border border-green/30"
    : "bg-bg-elevated text-text-tertiary border border-border";
  return (
    <span
      data-testid="ckpt-status-pill"
      className={`inline-block px-1.5 py-0.5 rounded text-[8px] uppercase tracking-wide ${cls}`}
    >
      {active ? "Active" : "Inactive"}
    </span>
  );
}

function EvalResultsCell({ er }: { er: Record<string, unknown> }) {
  const entries = Object.entries(er);
  if (entries.length === 0) {
    return <span className="text-text-tertiary">—</span>;
  }
  return (
    <div className="flex flex-wrap gap-1">
      {entries.map(([k, v]) => (
        <span
          key={k}
          className="inline-block px-1 py-0.5 rounded bg-bg-elevated text-[8px] text-text-secondary"
        >
          {k}: {typeof v === "number" ? v.toFixed(4) : String(v)}
        </span>
      ))}
    </div>
  );
}

export function MlCheckpoints() {
  const [items, setItems] = useState<readonly MlCheckpoint[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  const reload = useCallback(async (): Promise<void> => {
    try {
      const list = await api.adminListMlCheckpoints();
      setItems(list);
      setError(null);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const setActive = useCallback(
    async (id: number, isActive: boolean): Promise<void> => {
      setBusyId(id);
      try {
        await api.adminPatchMlCheckpoint(id, { is_active: isActive });
        await reload();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusyId(null);
      }
    },
    [reload],
  );

  const onDelete = useCallback(
    async (id: number): Promise<void> => {
      setBusyId(id);
      try {
        await api.adminDeleteMlCheckpoint(id);
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
    <Panel title="ML Checkpoints">
      {error !== null && (
        <div role="alert" className="text-red text-[10px] mb-2">
          {error}
        </div>
      )}
      {items === null ? (
        <div className="text-text-tertiary">Loading…</div>
      ) : items.length === 0 ? (
        <div className="text-text-tertiary">No checkpoints.</div>
      ) : (
        <table
          aria-label="ML checkpoints"
          className="w-full text-[10px] font-mono border-collapse"
        >
          <thead>
            <tr className="text-text-tertiary text-left uppercase tracking-wide">
              <th className="py-1 pr-2">Version</th>
              <th className="py-1 pr-2">Model</th>
              <th className="py-1 pr-2">Trained At</th>
              <th className="py-1 pr-2">SHA-256</th>
              <th className="py-1 pr-2">Status</th>
              <th className="py-1 pr-2">Eval</th>
              <th className="py-1 text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {items.map((c) => (
              <tr
                key={c.id}
                data-testid={`ckpt-row-${c.id}`}
                className="border-t border-border align-top"
              >
                <td className="py-1 pr-2">{c.version}</td>
                <td className="py-1 pr-2 text-text-secondary">
                  {c.model_name}
                </td>
                <td className="py-1 pr-2 text-text-secondary">
                  {fmtTs(c.trained_at)}
                </td>
                <td className="py-1 pr-2 text-text-tertiary">
                  {shortSha(c.sha256)}
                </td>
                <td className="py-1 pr-2">
                  <StatusPill active={c.is_active} />
                </td>
                <td className="py-1 pr-2">
                  <EvalResultsCell er={c.eval_results} />
                </td>
                <td className="py-1 text-right">
                  <div className="inline-flex gap-1">
                    {c.is_active ? (
                      <button
                        type="button"
                        disabled={busyId === c.id}
                        onClick={() => void setActive(c.id, false)}
                        className="min-h-[28px] px-2 text-[9px] uppercase tracking-wide bg-bg-elevated hover:bg-bg-base border border-border rounded disabled:opacity-50"
                      >
                        Deactivate
                      </button>
                    ) : (
                      <button
                        type="button"
                        disabled={busyId === c.id}
                        onClick={() => void setActive(c.id, true)}
                        className="min-h-[28px] px-2 text-[9px] uppercase tracking-wide bg-green/20 hover:bg-green/30 text-green border border-green/40 rounded disabled:opacity-50"
                      >
                        Activate
                      </button>
                    )}
                    <button
                      type="button"
                      disabled={busyId === c.id}
                      onClick={() => void onDelete(c.id)}
                      className="min-h-[28px] px-2 text-[9px] uppercase tracking-wide bg-bg-elevated hover:bg-red/20 border border-border rounded disabled:opacity-50"
                    >
                      Delete
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  );
}
