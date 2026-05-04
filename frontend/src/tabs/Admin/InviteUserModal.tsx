import { useState } from "react";
import { api, type Invitation } from "@/lib/api";

interface InviteUserModalProps {
  onClose: () => void;
  onCreated: (inv: Invitation) => void;
}

// SP-0.7 J2 per spec section 6.1: two-step invitation flow.
//   Step 1: form (email, display_name, is_admin, notes) -> POST /admin/invitations
//   Step 2: confirmation card showing the email + Copy button + the manual
//           Cloudflare Access copy-paste reminder. Closing returns to Users
//           list; the parent decides when to refetch.
export function InviteUserModal({ onClose, onCreated }: InviteUserModalProps) {
  const [step, setStep] = useState<"form" | "cf">("form");
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [isAdmin, setIsAdmin] = useState(false);
  const [notes, setNotes] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<Invitation | null>(null);
  const [copied, setCopied] = useState(false);

  const submit = async (e: React.FormEvent): Promise<void> => {
    e.preventDefault();
    if (submitting) return;
    setError(null);
    setSubmitting(true);
    try {
      const inv = await api.adminCreateInvitation({
        email: email.trim(),
        display_name: displayName.trim() || null,
        is_admin: isAdmin,
        notes: notes.trim() || null,
      });
      setCreated(inv);
      setStep("cf");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  const copyEmail = async (): Promise<void> => {
    if (created === null) return;
    try {
      await navigator.clipboard.writeText(created.email);
      setCopied(true);
    } catch {
      // Clipboard API may be unavailable in some contexts; ignore silently.
    }
  };

  return (
    <div
      role="dialog"
      aria-label="Invite user"
      data-testid="invite-modal"
      className="fixed inset-0 z-20 flex items-center justify-center bg-black/60 p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="bg-bg-panel border border-border rounded p-4 w-full max-w-md">
        {step === "form" ? (
          <form onSubmit={submit}>
            <h3 className="text-sm font-semibold mb-3">Invite a new user</h3>
            <label className="block text-[10px] uppercase tracking-wide text-text-tertiary mb-1">
              Email
              <input
                type="email"
                required
                autoFocus
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="block w-full mt-1 min-h-[44px] md:min-h-0 md:h-9 px-2 bg-bg-base border border-border rounded text-xs font-mono text-text-primary"
              />
            </label>
            <label className="block text-[10px] uppercase tracking-wide text-text-tertiary mb-1 mt-3">
              Display name
              <input
                type="text"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                className="block w-full mt-1 min-h-[44px] md:min-h-0 md:h-9 px-2 bg-bg-base border border-border rounded text-xs text-text-primary"
              />
            </label>
            <label className="block text-[10px] uppercase tracking-wide text-text-tertiary mb-1 mt-3">
              Notes
              <textarea
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                rows={2}
                className="block w-full mt-1 px-2 py-1 bg-bg-base border border-border rounded text-xs text-text-primary"
              />
            </label>
            <label className="flex items-center gap-2 mt-3 text-[11px]">
              <input
                type="checkbox"
                checked={isAdmin}
                onChange={(e) => setIsAdmin(e.target.checked)}
                className="h-4 w-4"
              />
              <span>Grant admin role</span>
            </label>
            {error !== null && (
              <div role="alert" className="text-red text-[10px] mt-2">
                {error}
              </div>
            )}
            <div className="flex justify-end gap-2 mt-4">
              <button
                type="button"
                onClick={onClose}
                className="min-h-[44px] md:min-h-0 md:h-9 px-3 text-xs uppercase tracking-wide bg-bg-elevated hover:bg-bg-base border border-border rounded"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={submitting}
                className="min-h-[44px] md:min-h-0 md:h-9 px-3 text-xs uppercase tracking-wide bg-green/15 text-green border border-green/30 rounded hover:bg-green/25 disabled:opacity-50"
              >
                {submitting ? "Inviting…" : "Invite"}
              </button>
            </div>
          </form>
        ) : (
          <div data-testid="invite-cf-step">
            <h3 className="text-sm font-semibold mb-3">
              Step 2 — Add to Cloudflare Access
            </h3>
            <p className="text-[11px] text-text-secondary mb-3">
              Trading Radar uses Cloudflare Access for authentication. The
              invitation row has been created in our database, but{" "}
              <span className="font-semibold text-text-primary">
                you must also add this email to the Cloudflare Access team
                policy
              </span>{" "}
              before the user can log in.
            </p>
            <div className="flex items-center gap-2 mb-3 bg-bg-base border border-border rounded p-2">
              <code className="flex-1 text-xs font-mono break-all">
                {created?.email ?? ""}
              </code>
              <button
                type="button"
                onClick={() => {
                  void copyEmail();
                }}
                className="min-h-[44px] md:min-h-0 md:h-7 px-2 text-[10px] uppercase tracking-wide bg-bg-elevated border border-border rounded hover:bg-bg-panel"
              >
                {copied ? "Copied" : "Copy"}
              </button>
            </div>
            <ol className="text-[11px] text-text-secondary list-decimal pl-4 space-y-1 mb-4">
              <li>Open the Cloudflare Zero Trust dashboard.</li>
              <li>Navigate to Access -&gt; Applications -&gt; Trading Radar.</li>
              <li>Edit the policy and add the copied email.</li>
              <li>Save the policy and confirm the user can authenticate.</li>
            </ol>
            <div className="flex justify-end">
              <button
                type="button"
                onClick={() => {
                  if (created !== null) onCreated(created);
                  else onClose();
                }}
                className="min-h-[44px] md:min-h-0 md:h-9 px-3 text-xs uppercase tracking-wide bg-green/15 text-green border border-green/30 rounded hover:bg-green/25"
              >
                I've added the email to CF Access
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
