/**
 * SP-8 Phase J — interactive mode switcher.
 *
 * Three buttons (Manual / Telegram-approve / Fully-auto). Click to
 * change. Downgrades fire immediately. Upgrades open a TOTP modal;
 * the backend computes gates server-side and rejects if they fail.
 *
 * Source of truth for the current mode is /api/v1/me — useCurrentUser
 * caches it module-scope so other panels stay in sync after a switch.
 */
import { useState } from "react";
import { Panel } from "@/components/ui/Panel";
import { useCurrentUser } from "@/hooks/useCurrentUser";
import { api, type TradingMode } from "@/lib/api";

const MODES: { id: TradingMode; label: string }[] = [
  { id: "manual", label: "Manual" },
  { id: "telegram-approve", label: "Telegram approve" },
  { id: "fully-auto", label: "Fully auto" },
];

const RANK: Record<TradingMode, number> = {
  manual: 0,
  "telegram-approve": 1,
  "fully-auto": 2,
};

export function ModeSwitcher() {
  const { user, reload } = useCurrentUser();
  const currentMode: TradingMode = user?.trading_mode ?? "manual";
  const totpConfigured = user?.totp_configured ?? false;

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<TradingMode | null>(null);
  const [totpInput, setTotpInput] = useState("");

  const isUpgrade = (target: TradingMode): boolean =>
    RANK[target] > RANK[currentMode];

  async function submit(target: TradingMode, totpCode?: string) {
    setBusy(true);
    setError(null);
    try {
      const body: { new_mode: TradingMode; totp_code?: string } = {
        new_mode: target,
      };
      if (totpCode !== undefined) body.totp_code = totpCode;
      await api.meChangeTradingMode(body);
      await reload();
      setPending(null);
      setTotpInput("");
    } catch (e: unknown) {
      const msg =
        e instanceof Error ? e.message : "mode change failed";
      setError(msg);
    } finally {
      setBusy(false);
    }
  }

  function onClick(target: TradingMode) {
    if (target === currentMode || busy) return;
    setError(null);
    if (isUpgrade(target)) {
      if (!totpConfigured) {
        setError(
          "Configure TOTP first (Settings → TOTP) before upgrading.",
        );
        return;
      }
      setPending(target);
      return;
    }
    void submit(target);
  }

  function onTotpSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!pending || totpInput.length !== 6) return;
    void submit(pending, totpInput);
  }

  return (
    <Panel title="Trading Mode">
      <div className="flex gap-1" role="group" aria-label="Trading mode">
        {MODES.map((m) => {
          const isActive = m.id === currentMode;
          return (
            <button
              key={m.id}
              type="button"
              onClick={() => onClick(m.id)}
              disabled={busy}
              aria-pressed={isActive}
              className={[
                "flex-1 px-3 py-2 text-[10px] uppercase tracking-wide rounded",
                "border-2 transition-colors font-bold",
                // Active state inverted (light bg + dark text) so the
                // current mode pops in the dark theme. The previous
                // bg-bg-panel vs bg-bg-base was a ~5% lightness delta
                // and operators couldn't tell which mode was selected.
                isActive
                  ? "bg-text-primary border-text-primary text-bg-base"
                  : "bg-bg-base border-border text-text-secondary hover:border-text-primary hover:text-text-primary",
                busy ? "opacity-50 cursor-not-allowed" : "cursor-pointer",
              ].join(" ")}
            >
              {m.label}
            </button>
          );
        })}
      </div>
      <p className="text-[8px] text-text-tertiary mt-1">
        Upgrades require TOTP + gates passing. Downgrades always allowed.
      </p>

      {error !== null ? (
        <div
          className="mt-2 p-2 rounded bg-red/10 border border-red/40"
          role="alert"
        >
          <p className="text-[10px] uppercase tracking-wide font-bold text-red mb-0.5">
            Mode change failed
          </p>
          <p className="text-[11px] text-red leading-snug">{error}</p>
          <p className="text-[9px] text-text-tertiary mt-1 leading-snug">
            Common causes: TOTP code wrong, promotion gates not yet
            satisfied (see Promotion Gates panel below), or the new mode
            requires admin override.
          </p>
        </div>
      ) : null}

      {pending !== null ? (
        <form onSubmit={onTotpSubmit} className="mt-2 space-y-1">
          <label className="text-[8px] uppercase text-text-tertiary block">
            Confirm with TOTP code to upgrade to {pending}
          </label>
          <div className="flex gap-1">
            <input
              type="text"
              inputMode="numeric"
              pattern="\d{6}"
              maxLength={6}
              autoFocus
              value={totpInput}
              onChange={(e) =>
                setTotpInput(e.target.value.replace(/\D/g, "").slice(0, 6))
              }
              aria-label="TOTP code"
              placeholder="123456"
              className="flex-1 px-2 py-1 text-[12px] rounded bg-bg-base border border-border"
            />
            <button
              type="submit"
              disabled={busy || totpInput.length !== 6}
              className="px-2 py-1 text-[10px] uppercase rounded bg-text-primary text-bg-base disabled:opacity-50"
            >
              Confirm
            </button>
            <button
              type="button"
              onClick={() => {
                setPending(null);
                setTotpInput("");
                setError(null);
              }}
              className="px-2 py-1 text-[10px] uppercase rounded border border-border text-text-secondary"
            >
              Cancel
            </button>
          </div>
        </form>
      ) : null}
    </Panel>
  );
}
