/**
 * SP-8 Phase J — interactive kill switches.
 *
 * Each switch shows enabled / tripped state. Click "disable" → opens
 * inline TOTP confirm. Click "enable" → fires immediately. Threshold
 * column shows the configured value (or default) — editing thresholds
 * is a future enhancement; this iteration covers the safety-critical
 * disable path.
 */
import { useEffect, useState } from "react";
import { Panel } from "@/components/ui/Panel";
import { useCurrentUser } from "@/hooks/useCurrentUser";
import {
  api,
  type KillSwitch,
  type KillSwitchName,
} from "@/lib/api";

const LABELS: Record<KillSwitchName, string> = {
  daily_loss: "Daily loss",
  consecutive_losses: "Consecutive losses",
  network_outage: "Network outage",
  slippage: "Slippage",
  liquidation_near: "Liquidation near",
  funding_rate_guard: "Funding rate guard",
};

const UNITS: Record<KillSwitchName, string> = {
  daily_loss: "% portfolio",
  consecutive_losses: "in a row",
  network_outage: "seconds",
  slippage: "%",
  liquidation_near: "% buffer",
  funding_rate_guard: "%/day",
};

function formatThreshold(s: KillSwitch): string {
  const v = s.threshold_value ?? s.default_threshold;
  return `${v} ${UNITS[s.name]}`;
}

export function KillSwitches() {
  const { user } = useCurrentUser();
  const totpConfigured = user?.totp_configured ?? false;

  const [switches, setSwitches] = useState<KillSwitch[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<KillSwitchName | null>(null);
  const [totpInput, setTotpInput] = useState("");
  const [busy, setBusy] = useState(false);

  async function reload() {
    setLoading(true);
    try {
      const out = await api.meKillSwitches();
      setSwitches(out.switches);
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "load failed");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void reload();
  }, []);

  async function flip(name: KillSwitchName, currentlyEnabled: boolean, totp?: string) {
    setBusy(true);
    setError(null);
    try {
      await api.meKillSwitchPatch(name, {
        enabled: !currentlyEnabled,
        totp_code: totp,
      });
      await reload();
      setPending(null);
      setTotpInput("");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "update failed");
    } finally {
      setBusy(false);
    }
  }

  function onClick(s: KillSwitch) {
    if (busy) return;
    if (s.enabled) {
      // Disable → require TOTP.
      if (!totpConfigured) {
        setError("Configure TOTP (Settings) before disabling switches.");
        return;
      }
      setPending(s.name);
    } else {
      void flip(s.name, false);
    }
  }

  function onTotpSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!pending || totpInput.length !== 6) return;
    void flip(pending, true, totpInput);
  }

  return (
    <Panel title="Kill Switches">
      {loading ? (
        <p className="text-[10px] text-text-tertiary">loading…</p>
      ) : (
        <table className="w-full text-[8.5px]">
          <thead>
            <tr className="text-text-tertiary text-left">
              <th className="font-normal pb-1">Switch</th>
              <th className="font-normal pb-1 text-right">Threshold</th>
              <th className="font-normal pb-1 text-right">State</th>
              <th className="font-normal pb-1 text-right">Action</th>
            </tr>
          </thead>
          <tbody>
            {switches.map((s) => (
              <tr key={s.name}>
                <td className="text-text-secondary">{LABELS[s.name]}</td>
                <td className="text-right text-text-secondary">
                  {formatThreshold(s)}
                </td>
                <td className="text-right">
                  {s.is_tripped ? (
                    <span className="text-red">TRIPPED</span>
                  ) : s.enabled ? (
                    <span className="text-green">armed</span>
                  ) : (
                    <span className="text-text-tertiary">disabled</span>
                  )}
                </td>
                <td className="text-right">
                  <button
                    type="button"
                    onClick={() => onClick(s)}
                    disabled={busy}
                    className={[
                      "px-2 py-0.5 text-[8.5px] uppercase rounded border",
                      s.enabled
                        ? "border-border text-text-secondary hover:text-text-primary"
                        : "border-text-primary text-text-primary",
                      busy ? "opacity-50 cursor-not-allowed" : "cursor-pointer",
                    ].join(" ")}
                    aria-label={
                      s.enabled
                        ? `disable ${LABELS[s.name]}`
                        : `enable ${LABELS[s.name]}`
                    }
                  >
                    {s.enabled ? "Disable" : "Enable"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="text-[8px] text-text-tertiary mt-1">
        Disabling requires TOTP. Tripped switches block new positions until reset.
      </p>

      {error !== null ? (
        <p className="text-[10px] text-text-bearish mt-1" role="alert">
          {error}
        </p>
      ) : null}

      {pending !== null ? (
        <form onSubmit={onTotpSubmit} className="mt-2 space-y-1">
          <label className="text-[8px] uppercase text-text-tertiary block">
            TOTP code to disable {LABELS[pending]}
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
