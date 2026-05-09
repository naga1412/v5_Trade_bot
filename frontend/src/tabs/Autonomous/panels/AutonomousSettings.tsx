/**
 * SP-8 Phase J — interactive Autonomous-Trading settings panel.
 *
 * Inline editors for the safety-relevant numeric settings (max
 * leverage cap, fixed sizing range). PATCH /me already accepts these
 * fields server-side. TOTP setup is left as a deep-link to the
 * existing settings flow until the dedicated wizard ships.
 *
 * Editing values is "soft" — saves to the user record but doesn't
 * require TOTP. Mode upgrades + kill-switch disables (the
 * safety-critical actions) handle hardware-confirm in their own
 * panels.
 */
import { useEffect, useState } from "react";
import { Panel } from "@/components/ui/Panel";
import { useCurrentUser } from "@/hooks/useCurrentUser";
import { api } from "@/lib/api";

interface DraftSettings {
  fixed_size_min_usdt: number;
  fixed_size_max_usdt: number;
  max_leverage_cap: number;
}

function userToDraft(
  user: ReturnType<typeof useCurrentUser>["user"],
): DraftSettings {
  return {
    fixed_size_min_usdt: user?.fixed_size_min_usdt ?? 30,
    fixed_size_max_usdt: user?.fixed_size_max_usdt ?? 30,
    max_leverage_cap: user?.max_leverage_cap ?? 10,
  };
}

export function AutonomousSettings() {
  const { user, reload } = useCurrentUser();
  const [draft, setDraft] = useState<DraftSettings>(userToDraft(user));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  // Sync the draft when the upstream user changes (after a save reload).
  useEffect(() => {
    setDraft(userToDraft(user));
  }, [user]);

  const dirty =
    draft.fixed_size_min_usdt !== (user?.fixed_size_min_usdt ?? 30) ||
    draft.fixed_size_max_usdt !== (user?.fixed_size_max_usdt ?? 30) ||
    draft.max_leverage_cap !== (user?.max_leverage_cap ?? 10);

  const valid =
    draft.fixed_size_min_usdt > 0 &&
    draft.fixed_size_max_usdt >= draft.fixed_size_min_usdt &&
    draft.max_leverage_cap >= 1 && draft.max_leverage_cap <= 20;

  async function save() {
    if (!dirty || !valid) return;
    setBusy(true);
    setError(null);
    try {
      await api.mePatch({
        fixed_size_min_usdt: draft.fixed_size_min_usdt,
        fixed_size_max_usdt: draft.fixed_size_max_usdt,
        max_leverage_cap: draft.max_leverage_cap,
      });
      await reload();
      setSavedAt(Date.now());
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "save failed");
    } finally {
      setBusy(false);
    }
  }

  const totpConfigured = user?.totp_configured ?? false;
  const binanceKeysConfigured = user?.binance_keys_configured ?? false;

  return (
    <Panel title="Settings">
      <div className="space-y-1 text-[9px]">
        <SettingsRow label="Position sizing">
          <span className="text-text-secondary">fixed</span>
        </SettingsRow>
        <SettingsRow label="Min size (USDT)">
          <NumberInput
            value={draft.fixed_size_min_usdt}
            onChange={(v) =>
              setDraft({ ...draft, fixed_size_min_usdt: v })
            }
            min={1}
            ariaLabel="min size USDT"
          />
        </SettingsRow>
        <SettingsRow label="Max size (USDT)">
          <NumberInput
            value={draft.fixed_size_max_usdt}
            onChange={(v) =>
              setDraft({ ...draft, fixed_size_max_usdt: v })
            }
            min={draft.fixed_size_min_usdt}
            ariaLabel="max size USDT"
          />
        </SettingsRow>
        <SettingsRow label="Max leverage">
          <NumberInput
            value={draft.max_leverage_cap}
            onChange={(v) =>
              setDraft({ ...draft, max_leverage_cap: v })
            }
            min={1}
            max={20}
            ariaLabel="max leverage"
          />
        </SettingsRow>
        <SettingsRow label="API key vault">
          <span
            className={
              binanceKeysConfigured ? "text-green" : "text-text-tertiary"
            }
          >
            {binanceKeysConfigured ? "configured" : "not set"}
          </span>
        </SettingsRow>
        <SettingsRow label="TOTP">
          <span
            className={
              totpConfigured ? "text-green" : "text-text-tertiary"
            }
          >
            {totpConfigured ? "configured" : "not set up"}
          </span>
        </SettingsRow>
      </div>

      <div className="mt-2 flex items-center gap-1">
        <button
          type="button"
          onClick={save}
          disabled={!dirty || !valid || busy}
          className={[
            "px-2 py-1 text-[9px] uppercase tracking-wide rounded",
            dirty && valid && !busy
              ? "bg-text-primary text-bg-base cursor-pointer"
              : "border border-border text-text-tertiary cursor-not-allowed",
          ].join(" ")}
        >
          {busy ? "saving…" : "Save"}
        </button>
        {savedAt !== null && !dirty ? (
          <span className="text-[8px] text-text-tertiary">saved ✓</span>
        ) : null}
      </div>

      {error !== null ? (
        <p className="text-[10px] text-text-bearish mt-1" role="alert">
          {error}
        </p>
      ) : null}

      <p className="text-[8px] text-text-tertiary mt-1">
        Numeric edits save instantly. Mode upgrades + kill-switch
        disables require TOTP — handled in their own panels above.
      </p>
    </Panel>
  );
}

function SettingsRow({
  label, children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-text-secondary">{label}</span>
      <span className="text-right">{children}</span>
    </div>
  );
}

function NumberInput({
  value, onChange, min, max, ariaLabel,
}: {
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
  ariaLabel: string;
}) {
  return (
    <input
      type="number"
      value={value}
      min={min}
      max={max}
      step={1}
      onChange={(e) => {
        const n = Number(e.target.value);
        if (!Number.isNaN(n)) onChange(n);
      }}
      aria-label={ariaLabel}
      className="w-20 px-1 py-0.5 text-right text-[10px] bg-bg-base border border-border rounded"
    />
  );
}
