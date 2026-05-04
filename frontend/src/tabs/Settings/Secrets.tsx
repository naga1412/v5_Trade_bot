import { useState } from "react";
import { Panel } from "@/components/ui/Panel";
import { useCurrentUser } from "@/hooks/useCurrentUser";
import { api, type TotpSetupOut } from "@/lib/api";

// SP-0.7 K3: three sub-cards.
//   1. Binance keys      — POST /me/binance-keys
//   2. Telegram          — POST /me/telegram
//   3. TOTP setup/verify — POST /me/totp/setup -> show provisioning URI +
//                          backup codes (ONCE) -> POST /me/totp/verify
//
// We deliberately do not pull a QR-code library in to keep the dep tree tight
// (cross-cutting requirement: no new dependencies). The provisioning URI is
// rendered as a code block alongside the human-readable secret so the user
// can copy it into Authy / Google Authenticator manually, or paste it into
// any QR-rendering tool of their choice. SP-1 may add a bundled QR component.

interface ConfiguredBadgeProps {
  configured: boolean;
}

function ConfiguredBadge({ configured }: ConfiguredBadgeProps) {
  if (configured) {
    return (
      <span
        data-testid="configured-badge"
        className="inline-block px-1.5 py-0.5 rounded text-[8px] uppercase tracking-wide bg-green/15 text-green border border-green/30"
      >
        Configured
      </span>
    );
  }
  return (
    <span
      data-testid="not-configured-badge"
      className="inline-block px-1.5 py-0.5 rounded text-[8px] uppercase tracking-wide bg-text-tertiary/15 text-text-tertiary border border-text-tertiary/30"
    >
      Not configured
    </span>
  );
}

function BinanceCard({ configured, onSaved }: { configured: boolean; onSaved: () => void }) {
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  const onSubmit = async (e: React.FormEvent): Promise<void> => {
    e.preventDefault();
    if (saving) return;
    setSaving(true);
    setError(null);
    setStatus(null);
    try {
      await api.meBinanceKeys(apiKey, apiSecret);
      setApiKey("");
      setApiSecret("");
      setStatus("Binance keys saved.");
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Panel
      title="Binance API keys"
      rightSlot={<ConfiguredBadge configured={configured} />}
    >
      <form onSubmit={onSubmit} className="space-y-2">
        <label className="block">
          <span className="text-[10px] uppercase tracking-wide text-text-tertiary">
            API key
          </span>
          <input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            autoComplete="off"
            className="block w-full mt-1 min-h-[44px] md:min-h-0 md:h-9 px-2 bg-bg-base border border-border rounded text-xs font-mono text-text-primary"
          />
        </label>
        <label className="block">
          <span className="text-[10px] uppercase tracking-wide text-text-tertiary">
            API secret
          </span>
          <input
            type="password"
            value={apiSecret}
            onChange={(e) => setApiSecret(e.target.value)}
            autoComplete="off"
            className="block w-full mt-1 min-h-[44px] md:min-h-0 md:h-9 px-2 bg-bg-base border border-border rounded text-xs font-mono text-text-primary"
          />
        </label>
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
            disabled={saving || !apiKey || !apiSecret}
            className="min-h-[44px] md:min-h-0 md:h-9 px-3 text-xs uppercase tracking-wide bg-green/15 text-green border border-green/30 rounded hover:bg-green/25 disabled:opacity-50"
          >
            {saving ? "Saving…" : "Save Binance keys"}
          </button>
        </div>
      </form>
    </Panel>
  );
}

function TelegramCard({ configured, onSaved }: { configured: boolean; onSaved: () => void }) {
  const [botToken, setBotToken] = useState("");
  const [chatId, setChatId] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  const onSubmit = async (e: React.FormEvent): Promise<void> => {
    e.preventDefault();
    if (saving) return;
    setSaving(true);
    setError(null);
    setStatus(null);
    try {
      await api.meTelegram(botToken, chatId);
      setBotToken("");
      setChatId("");
      setStatus("Telegram credentials saved.");
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Panel
      title="Telegram notifications"
      rightSlot={<ConfiguredBadge configured={configured} />}
    >
      <form onSubmit={onSubmit} className="space-y-2">
        <label className="block">
          <span className="text-[10px] uppercase tracking-wide text-text-tertiary">
            Bot token
          </span>
          <input
            type="password"
            value={botToken}
            onChange={(e) => setBotToken(e.target.value)}
            autoComplete="off"
            className="block w-full mt-1 min-h-[44px] md:min-h-0 md:h-9 px-2 bg-bg-base border border-border rounded text-xs font-mono text-text-primary"
          />
        </label>
        <label className="block">
          <span className="text-[10px] uppercase tracking-wide text-text-tertiary">
            Chat ID
          </span>
          <input
            type="text"
            value={chatId}
            onChange={(e) => setChatId(e.target.value)}
            className="block w-full mt-1 min-h-[44px] md:min-h-0 md:h-9 px-2 bg-bg-base border border-border rounded text-xs font-mono text-text-primary"
          />
        </label>
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
            disabled={saving || !botToken || !chatId}
            className="min-h-[44px] md:min-h-0 md:h-9 px-3 text-xs uppercase tracking-wide bg-green/15 text-green border border-green/30 rounded hover:bg-green/25 disabled:opacity-50"
          >
            {saving ? "Saving…" : "Save Telegram"}
          </button>
        </div>
      </form>
    </Panel>
  );
}

function TotpCard({ configured, onSetupComplete }: { configured: boolean; onSetupComplete: () => void }) {
  const [setup, setSetup] = useState<TotpSetupOut | null>(null);
  const [verifyCode, setVerifyCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [verified, setVerified] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onStartSetup = async (): Promise<void> => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const out = await api.meTotpSetup();
      setSetup(out);
      setVerified(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const onVerify = async (e: React.FormEvent): Promise<void> => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const out = await api.meTotpVerify(verifyCode);
      if (out.ok) {
        setVerified(true);
        setSetup(null);
        setVerifyCode("");
        onSetupComplete();
      } else {
        setError("Invalid TOTP code. Re-enter from your authenticator.");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel
      title="Two-factor authentication (TOTP)"
      rightSlot={<ConfiguredBadge configured={configured || verified} />}
    >
      {setup === null ? (
        <div className="space-y-2">
          <p className="text-text-secondary text-[10px]">
            {configured
              ? "TOTP is already configured. Click below to rotate the secret."
              : "Add an authenticator (Authy, Google Authenticator, 1Password) to enable approval flows."}
          </p>
          {error !== null && (
            <div role="alert" className="text-red text-[10px]">
              {error}
            </div>
          )}
          <div className="flex justify-end">
            <button
              type="button"
              onClick={() => {
                void onStartSetup();
              }}
              disabled={busy}
              className="min-h-[44px] md:min-h-0 md:h-9 px-3 text-xs uppercase tracking-wide bg-bg-elevated border border-border rounded hover:bg-bg-base disabled:opacity-50"
            >
              {busy ? "Working…" : configured ? "Rotate TOTP secret" : "Set up TOTP"}
            </button>
          </div>
        </div>
      ) : (
        <div data-testid="totp-setup-panel" className="space-y-3">
          <div>
            <div className="text-[10px] uppercase tracking-wide text-text-tertiary mb-1">
              Step 1 — Add to your authenticator app
            </div>
            <p className="text-text-secondary text-[10px] mb-1">
              Either scan the URI as a QR code or paste it directly into your
              authenticator app. The secret is also displayed in case manual
              entry is required.
            </p>
            <code
              data-testid="totp-provisioning-uri"
              className="block w-full bg-bg-base border border-border rounded p-2 text-[10px] font-mono break-all"
            >
              {setup.provisioning_uri}
            </code>
            <div className="text-[10px] mt-1">
              <span className="text-text-tertiary">Secret:</span>{" "}
              <code className="font-mono" data-testid="totp-secret-display">
                {setup.secret_for_display}
              </code>
            </div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wide text-text-tertiary mb-1">
              Step 2 — Save your backup codes
            </div>
            <div role="alert" className="text-red text-[10px] mb-2">
              Save these now — they will not be shown again.
            </div>
            <ul
              data-testid="totp-backup-codes"
              className="grid grid-cols-2 gap-1 text-[10px] font-mono bg-bg-base border border-border rounded p-2"
            >
              {setup.backup_codes.map((c) => (
                <li key={c}>{c}</li>
              ))}
            </ul>
          </div>
          <form onSubmit={onVerify} className="space-y-2">
            <label className="block">
              <span className="text-[10px] uppercase tracking-wide text-text-tertiary">
                Step 3 — Verify with a 6-digit code from your authenticator
              </span>
              <input
                type="text"
                inputMode="numeric"
                pattern="[0-9]*"
                value={verifyCode}
                onChange={(e) => setVerifyCode(e.target.value)}
                maxLength={6}
                className="block w-full mt-1 min-h-[44px] md:min-h-0 md:h-9 px-2 bg-bg-base border border-border rounded text-xs font-mono text-text-primary"
              />
            </label>
            {error !== null && (
              <div role="alert" className="text-red text-[10px]">
                {error}
              </div>
            )}
            <div className="flex justify-end">
              <button
                type="submit"
                disabled={busy || verifyCode.length < 6}
                className="min-h-[44px] md:min-h-0 md:h-9 px-3 text-xs uppercase tracking-wide bg-green/15 text-green border border-green/30 rounded hover:bg-green/25 disabled:opacity-50"
              >
                {busy ? "Verifying…" : "Verify"}
              </button>
            </div>
          </form>
        </div>
      )}
    </Panel>
  );
}

export function Secrets() {
  const { user, reload } = useCurrentUser();

  if (user === null) {
    return <div className="text-text-tertiary text-xs">Loading…</div>;
  }

  const refreshUser = (): void => {
    void reload();
  };

  return (
    <div className="space-y-3">
      <BinanceCard configured={user.binance_keys_configured} onSaved={refreshUser} />
      <TelegramCard configured={user.telegram_configured} onSaved={refreshUser} />
      <TotpCard configured={user.totp_configured} onSetupComplete={refreshUser} />
    </div>
  );
}
