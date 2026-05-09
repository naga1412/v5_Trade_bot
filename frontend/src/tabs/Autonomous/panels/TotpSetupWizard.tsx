/**
 * SP-8 Phase J — TOTP setup wizard.
 *
 * Three-step flow:
 *   1. Click "Set up TOTP" → backend calls /me/totp/setup which generates
 *      a fresh secret + 10 backup codes. Wizard renders QR code + secret
 *      string + downloadable backup codes (one-time view).
 *   2. User scans QR with Authy / Google Authenticator + saves backup codes.
 *   3. User types the current 6-digit code; wizard calls /me/totp/verify.
 *      On success, the user's totp_configured flag flips and the wizard
 *      collapses back to the "Configured ✓" idle state.
 *
 * Re-running setup overwrites the existing secret + backup codes —
 * the wizard warns about this in the confirm dialog.
 */
import { useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import { Panel } from "@/components/ui/Panel";
import { useCurrentUser } from "@/hooks/useCurrentUser";
import { api, type TotpSetupOut } from "@/lib/api";

type WizardState =
  | { stage: "idle" }
  | { stage: "confirm-overwrite" }
  | { stage: "setup-loading" }
  | { stage: "verify"; setup: TotpSetupOut }
  | { stage: "done" };

export function TotpSetupWizard() {
  const { user, reload } = useCurrentUser();
  const totpConfigured = user?.totp_configured ?? false;
  const [state, setState] = useState<WizardState>({ stage: "idle" });
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function startSetup() {
    setBusy(true);
    setError(null);
    setState({ stage: "setup-loading" });
    try {
      const setup = await api.meTotpSetup();
      setState({ stage: "verify", setup });
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "setup failed");
      setState({ stage: "idle" });
    } finally {
      setBusy(false);
    }
  }

  function onSetupClick() {
    setError(null);
    if (totpConfigured) {
      setState({ stage: "confirm-overwrite" });
      return;
    }
    void startSetup();
  }

  async function verify() {
    if (state.stage !== "verify" || code.length !== 6) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api.meTotpVerify(code);
      if (!r.ok) {
        setError("invalid code; check your authenticator clock + try again");
        return;
      }
      await reload();
      setState({ stage: "done" });
      setCode("");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "verify failed");
    } finally {
      setBusy(false);
    }
  }

  function cancel() {
    setState({ stage: "idle" });
    setCode("");
    setError(null);
  }

  return (
    <Panel title="TOTP (Two-Factor)">
      {state.stage === "idle" || state.stage === "done" ? (
        <div className="space-y-1">
          <p className="text-[9px] text-text-secondary">
            Status:{" "}
            <span
              className={
                totpConfigured ? "text-green" : "text-text-tertiary"
              }
            >
              {totpConfigured ? "configured ✓" : "not set up"}
            </span>
          </p>
          <button
            type="button"
            onClick={onSetupClick}
            disabled={busy}
            className="mt-1 w-full px-2 py-1 text-[9px] uppercase tracking-wide rounded border border-text-primary bg-bg-base text-text-primary"
          >
            {totpConfigured ? "Re-run setup" : "Set up TOTP"}
          </button>
          <p className="text-[8px] text-text-tertiary mt-1">
            TOTP gates mode upgrades + kill-switch disables. Required
            before going beyond manual mode.
          </p>
        </div>
      ) : null}

      {state.stage === "confirm-overwrite" ? (
        <div className="space-y-1">
          <p className="text-[10px] text-yellow" role="alert">
            Re-running setup invalidates the current secret + all backup
            codes. Proceed?
          </p>
          <div className="flex gap-1">
            <button
              type="button"
              onClick={() => void startSetup()}
              className="flex-1 px-2 py-1 text-[9px] uppercase rounded bg-text-primary text-bg-base"
            >
              Yes, regenerate
            </button>
            <button
              type="button"
              onClick={cancel}
              className="flex-1 px-2 py-1 text-[9px] uppercase rounded border border-border text-text-secondary"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : null}

      {state.stage === "setup-loading" ? (
        <p className="text-[10px] text-text-tertiary">generating secret…</p>
      ) : null}

      {state.stage === "verify" ? (
        <div className="space-y-2">
          <p className="text-[9px] text-text-secondary">
            Scan this QR with Authy / Google Authenticator:
          </p>
          <div className="flex justify-center bg-white p-2 rounded">
            <QRCodeSVG
              value={state.setup.provisioning_uri}
              size={140}
              aria-label="TOTP provisioning QR code"
            />
          </div>
          <details className="text-[9px]">
            <summary className="cursor-pointer text-text-tertiary">
              Or paste the URI manually
            </summary>
            <code className="block mt-1 p-1 break-all text-[8.5px] bg-bg-base rounded border border-border">
              {state.setup.provisioning_uri}
            </code>
            <p className="text-[8px] text-text-tertiary mt-1">
              Secret: <code>{state.setup.secret_for_display}</code>
            </p>
          </details>
          <div>
            <p className="text-[9px] text-text-secondary mb-1">
              Backup codes (save these — one-time view):
            </p>
            <div
              className="grid grid-cols-2 gap-1 text-[9px] font-mono"
              role="list"
              aria-label="backup codes"
            >
              {state.setup.backup_codes.map((c) => (
                <code
                  key={c}
                  role="listitem"
                  className="px-1 py-0.5 bg-bg-base border border-border rounded text-center"
                >
                  {c}
                </code>
              ))}
            </div>
          </div>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void verify();
            }}
            className="space-y-1"
          >
            <label
              htmlFor="totp-verify-code"
              className="text-[8px] uppercase text-text-tertiary block"
            >
              Enter the current 6-digit code
            </label>
            <div className="flex gap-1">
              <input
                id="totp-verify-code"
                type="text"
                inputMode="numeric"
                pattern="\d{6}"
                maxLength={6}
                autoFocus
                value={code}
                onChange={(e) =>
                  setCode(e.target.value.replace(/\D/g, "").slice(0, 6))
                }
                aria-label="TOTP verification code"
                placeholder="123456"
                className="flex-1 px-2 py-1 text-[12px] rounded bg-bg-base border border-border"
              />
              <button
                type="submit"
                disabled={busy || code.length !== 6}
                className="px-2 py-1 text-[10px] uppercase rounded bg-text-primary text-bg-base disabled:opacity-50"
              >
                Verify
              </button>
              <button
                type="button"
                onClick={cancel}
                className="px-2 py-1 text-[10px] uppercase rounded border border-border text-text-secondary"
              >
                Cancel
              </button>
            </div>
          </form>
        </div>
      ) : null}

      {error !== null ? (
        <p className="text-[10px] text-text-bearish mt-1" role="alert">
          {error}
        </p>
      ) : null}

      {state.stage === "done" ? (
        <p className="text-[10px] text-green mt-1" role="status">
          ✓ TOTP verified. You can now upgrade modes + disable kill switches.
        </p>
      ) : null}
    </Panel>
  );
}
