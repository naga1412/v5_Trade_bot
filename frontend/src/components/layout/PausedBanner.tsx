import { useEffect, useState } from "react";
import { getSystemState, type SystemPauseState } from "@/lib/api";

const POLL_INTERVAL_MS = 5_000;

// SP-PAUSE: global yellow warning banner mounted in App.tsx. Self-polls
// /admin/system/state every 5s; renders null when system is running so it
// has zero DOM cost in the normal case.
export function PausedBanner() {
  const [state, setState] = useState<SystemPauseState | null>(null);

  useEffect(() => {
    let cancelled = false;
    const refresh = async (): Promise<void> => {
      try {
        const s = await getSystemState();
        if (!cancelled) setState(s);
      } catch {
        // Swallow — banner stays in last-known state on transient errors.
      }
    };
    void refresh();
    const id = setInterval(() => { void refresh(); }, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  if (state === null || !state.paused) return null;

  return (
    <div
      role="banner"
      data-testid="paused-banner"
      className="flex items-center justify-between bg-yellow/20 border-b border-yellow text-yellow px-3 py-2"
    >
      <div className="text-sm font-mono">
        <span className="font-semibold">System paused.</span> Trading + ingest workers idle. Read-only mode.
      </div>
      <a
        href="#/settings"
        className="min-h-[44px] md:min-h-0 md:h-8 px-3 text-xs uppercase tracking-wide bg-yellow/30 hover:bg-yellow/40 rounded border border-yellow flex items-center"
      >
        Resume in Settings
      </a>
    </div>
  );
}
