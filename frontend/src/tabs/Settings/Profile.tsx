import { useEffect, useState } from "react";
import { Panel } from "@/components/ui/Panel";
import { useCurrentUser } from "@/hooks/useCurrentUser";
import { api } from "@/lib/api";

// SP-0.7 K1: Profile sub-page. Form for display_name + quiet_hours_*. On save
// PATCH /me, then reload the shared current-user cache so the rest of the app
// (banner email, TabNav adminVisible) stays in sync.

export function Profile() {
  const { user, reload } = useCurrentUser();
  const [displayName, setDisplayName] = useState("");
  const [quietEnabled, setQuietEnabled] = useState(false);
  const [quietStart, setQuietStart] = useState("");
  const [quietEnd, setQuietEnd] = useState("");
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Re-seed local form state every time the cached user changes (initial load,
  // or after a successful PATCH that triggers reload()).
  useEffect(() => {
    if (user === null) return;
    setDisplayName(user.display_name);
    setQuietEnabled(user.quiet_hours_enabled);
    setQuietStart(user.quiet_hours_start ?? "");
    setQuietEnd(user.quiet_hours_end ?? "");
  }, [user]);

  if (user === null) {
    return <div className="text-text-tertiary text-xs">Loading…</div>;
  }

  const onSubmit = async (e: React.FormEvent): Promise<void> => {
    e.preventDefault();
    if (saving) return;
    setSaving(true);
    setError(null);
    setStatus(null);
    try {
      await api.mePatch({
        display_name: displayName,
        quiet_hours_enabled: quietEnabled,
        quiet_hours_start: quietStart === "" ? null : quietStart,
        quiet_hours_end: quietEnd === "" ? null : quietEnd,
      });
      setStatus("Saved.");
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Panel title="Profile">
      <form onSubmit={onSubmit} className="space-y-3">
        <div>
          <label className="text-[10px] uppercase tracking-wide text-text-tertiary block">
            Email
          </label>
          <div className="text-xs font-mono text-text-secondary py-2">
            {user.email}
          </div>
        </div>
        <label className="block">
          <span className="text-[10px] uppercase tracking-wide text-text-tertiary">
            Display name
          </span>
          <input
            type="text"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            className="block w-full mt-1 min-h-[44px] md:min-h-0 md:h-9 px-2 bg-bg-base border border-border rounded text-xs font-mono text-text-primary"
          />
        </label>
        <fieldset className="border border-border rounded p-2">
          <legend className="text-[10px] uppercase tracking-wide text-text-tertiary px-1">
            Quiet hours
          </legend>
          <label className="flex items-center gap-2 text-[11px]">
            <input
              type="checkbox"
              checked={quietEnabled}
              onChange={(e) => setQuietEnabled(e.target.checked)}
              className="h-4 w-4"
            />
            <span>Enable quiet hours (suppress notifications)</span>
          </label>
          <div className="grid grid-cols-2 gap-2 mt-2">
            <label className="block">
              <span className="text-[10px] uppercase tracking-wide text-text-tertiary">
                Start (HH:MM)
              </span>
              <input
                type="time"
                value={quietStart}
                onChange={(e) => setQuietStart(e.target.value)}
                className="block w-full mt-1 min-h-[44px] md:min-h-0 md:h-9 px-2 bg-bg-base border border-border rounded text-xs font-mono text-text-primary"
              />
            </label>
            <label className="block">
              <span className="text-[10px] uppercase tracking-wide text-text-tertiary">
                End (HH:MM)
              </span>
              <input
                type="time"
                value={quietEnd}
                onChange={(e) => setQuietEnd(e.target.value)}
                className="block w-full mt-1 min-h-[44px] md:min-h-0 md:h-9 px-2 bg-bg-base border border-border rounded text-xs font-mono text-text-primary"
              />
            </label>
          </div>
        </fieldset>
        {error !== null && (
          <div role="alert" className="text-red text-[10px]">
            {error}
          </div>
        )}
        {status !== null && (
          <div role="status" className="text-green text-[10px]">
            {status}
          </div>
        )}
        <div className="flex justify-end">
          <button
            type="submit"
            disabled={saving}
            className="min-h-[44px] md:min-h-0 md:h-9 px-3 text-xs uppercase tracking-wide bg-green/15 text-green border border-green/30 rounded hover:bg-green/25 disabled:opacity-50"
          >
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </form>
    </Panel>
  );
}
