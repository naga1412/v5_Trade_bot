import { useEffect, useState } from "react";
import { Panel } from "@/components/ui/Panel";
import { useCurrentUser } from "@/hooks/useCurrentUser";
import { api } from "@/lib/api";

// SP-0.7 K2: Trading sub-page. Position-sizing config (fixed $20-$50 default),
// max_concurrent_positions, max_leverage_cap. Trading mode is read-only until
// SP-8 ships. Kill-switch section is a placeholder card.

function toNullableNumber(s: string): number | null {
  if (s === "") return null;
  const n = Number(s);
  return Number.isFinite(n) ? n : null;
}

function toStr(n: number | null): string {
  return n === null ? "" : String(n);
}

export function Trading() {
  const { user, reload } = useCurrentUser();
  const [minUsdt, setMinUsdt] = useState("");
  const [maxUsdt, setMaxUsdt] = useState("");
  const [maxConcurrent, setMaxConcurrent] = useState("");
  const [maxLeverage, setMaxLeverage] = useState("");
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (user === null) return;
    setMinUsdt(toStr(user.fixed_size_min_usdt));
    setMaxUsdt(toStr(user.fixed_size_max_usdt));
    setMaxConcurrent(toStr(user.max_concurrent_positions));
    setMaxLeverage(toStr(user.max_leverage_cap));
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
        fixed_size_min_usdt: toNullableNumber(minUsdt),
        fixed_size_max_usdt: toNullableNumber(maxUsdt),
        max_concurrent_positions: toNullableNumber(maxConcurrent),
        max_leverage_cap: toNullableNumber(maxLeverage),
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
    <div className="space-y-3">
      <Panel title="Trading mode">
        <div className="text-xs">
          <div>
            <span className="text-text-tertiary uppercase tracking-wide text-[10px] mr-2">
              Mode
            </span>
            <span
              data-testid="trading-mode-readonly"
              className="font-mono text-text-primary"
            >
              {user.trading_mode}
            </span>
          </div>
          <p className="text-text-tertiary text-[10px] mt-2">
            Mode is locked at <code>manual</code> for SP-0.7. The
            telegram-approve and fully-auto modes unlock in SP-8 once the
            promotion gate has passed.
          </p>
        </div>
      </Panel>

      <Panel title="Position sizing">
        <form onSubmit={onSubmit} className="space-y-3">
          <div className="grid grid-cols-2 gap-2">
            <label className="block">
              <span className="text-[10px] uppercase tracking-wide text-text-tertiary">
                Fixed size min (USDT)
              </span>
              <input
                type="number"
                step="0.01"
                value={minUsdt}
                onChange={(e) => setMinUsdt(e.target.value)}
                className="block w-full mt-1 min-h-[44px] md:min-h-0 md:h-9 px-2 bg-bg-base border border-border rounded text-xs font-mono text-text-primary"
              />
            </label>
            <label className="block">
              <span className="text-[10px] uppercase tracking-wide text-text-tertiary">
                Fixed size max (USDT)
              </span>
              <input
                type="number"
                step="0.01"
                value={maxUsdt}
                onChange={(e) => setMaxUsdt(e.target.value)}
                className="block w-full mt-1 min-h-[44px] md:min-h-0 md:h-9 px-2 bg-bg-base border border-border rounded text-xs font-mono text-text-primary"
              />
            </label>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <label className="block">
              <span className="text-[10px] uppercase tracking-wide text-text-tertiary">
                Max concurrent positions
              </span>
              <input
                type="number"
                min={1}
                value={maxConcurrent}
                onChange={(e) => setMaxConcurrent(e.target.value)}
                className="block w-full mt-1 min-h-[44px] md:min-h-0 md:h-9 px-2 bg-bg-base border border-border rounded text-xs font-mono text-text-primary"
              />
            </label>
            <label className="block">
              <span className="text-[10px] uppercase tracking-wide text-text-tertiary">
                Max leverage cap
              </span>
              <input
                type="number"
                min={1}
                value={maxLeverage}
                onChange={(e) => setMaxLeverage(e.target.value)}
                className="block w-full mt-1 min-h-[44px] md:min-h-0 md:h-9 px-2 bg-bg-base border border-border rounded text-xs font-mono text-text-primary"
              />
            </label>
          </div>
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

      <Panel title="Kill switches">
        <div className="text-text-tertiary text-[10px]">
          (Coming in SP-8)
        </div>
      </Panel>
    </div>
  );
}
