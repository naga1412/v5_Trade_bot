import { useEffect, useState } from "react";
import { Panel } from "@/components/ui/Panel";
import {
  getSystemState,
  pauseSystem,
  resumeSystem,
  type SystemPauseState,
} from "@/lib/api";

const POLL_INTERVAL_MS = 5_000;

export function SystemPauseControl() {
  const [state, setState] = useState<SystemPauseState | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const refresh = async (): Promise<void> => {
      try {
        const s = await getSystemState();
        if (!cancelled) setState(s);
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e));
        }
      }
    };
    void refresh();
    const id = setInterval(() => { void refresh(); }, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const onPause = async (): Promise<void> => {
    if (busy || reason.trim() === "") return;
    setBusy(true);
    setError(null);
    try {
      const s = await pauseSystem(reason.trim());
      setState(s);
      setReason("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const onResume = async (): Promise<void> => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const s = await resumeSystem();
      setState(s);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (state === null) {
    return (
      <Panel title="System Pause">
        <div className="text-text-tertiary text-xs">Loading…</div>
      </Panel>
    );
  }

  if (state.paused) {
    return (
      <Panel title="System Pause">
        <div className="space-y-3">
          <div
            data-testid="paused-status"
            className="bg-yellow/10 border border-yellow text-yellow px-3 py-2 rounded text-xs font-mono"
          >
            <div>System paused.</div>
            {state.since !== null && state.by_email !== null && (
              <div>
                Paused since {state.since} UTC by{" "}
                <span className="font-semibold">{state.by_email}</span>
              </div>
            )}
            {state.reason !== null && (
              <div>Reason: {state.reason}</div>
            )}
          </div>
          <button
            type="button"
            onClick={() => { void onResume(); }}
            disabled={busy}
            className="min-h-[44px] md:min-h-0 md:h-9 px-4 bg-green text-white rounded text-sm font-mono uppercase tracking-wide disabled:opacity-50"
          >
            Resume
          </button>
          {error !== null && (
            <div className="text-red text-xs font-mono">{error}</div>
          )}
        </div>
      </Panel>
    );
  }

  return (
    <Panel title="System Pause">
      <div className="space-y-3">
        <div className="text-xs text-text-secondary">
          System running normally. Pause halts all background workers and
          returns 423 on non-admin POSTs until you resume.
        </div>
        <textarea
          aria-label="Pause reason"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="Reason (required) — e.g., travel, broker outage"
          className="w-full h-20 bg-bg-base border border-border rounded p-2 text-xs font-mono"
        />
        <button
          type="button"
          onClick={() => { void onPause(); }}
          disabled={busy || reason.trim() === ""}
          className="min-h-[44px] md:min-h-0 md:h-9 px-4 bg-red text-white rounded text-sm font-mono uppercase tracking-wide disabled:opacity-50"
        >
          Pause
        </button>
        {error !== null && (
          <div className="text-red text-xs font-mono">{error}</div>
        )}
      </div>
    </Panel>
  );
}
