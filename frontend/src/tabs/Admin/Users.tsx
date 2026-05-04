import { useCallback, useEffect, useState } from "react";
import { Panel } from "@/components/ui/Panel";
import { api, type AdminUser } from "@/lib/api";
import { InviteUserModal } from "@/tabs/Admin/InviteUserModal";
import { UserRowMenu } from "@/tabs/Admin/UserRowMenu";

// SP-0.7 Phase J (task J1) per spec section 9.1: Admin -> Users table.
// Columns: Email | Last Login | Status | Mode | [Admin] | Actions(gear).

function fmtTs(iso: string | null): string {
  if (iso === null) return "—";
  try {
    return new Date(iso).toISOString().replace("T", " ").slice(0, 16) + "Z";
  } catch {
    return iso;
  }
}

function statusOf(u: AdminUser): "Active" | "Revoked" {
  return u.is_active ? "Active" : "Revoked";
}

function StatusPill({ status }: { status: "Active" | "Revoked" }) {
  const cls =
    status === "Active"
      ? "bg-green/15 text-green border border-green/30"
      : "bg-red/15 text-red border border-red/30";
  return (
    <span
      data-testid="status-pill"
      className={`inline-block px-1.5 py-0.5 rounded text-[8px] uppercase tracking-wide ${cls}`}
    >
      {status}
    </span>
  );
}

export function Users() {
  const [users, setUsers] = useState<readonly AdminUser[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showInvite, setShowInvite] = useState(false);

  const reload = useCallback(async (): Promise<void> => {
    try {
      const list = await api.adminListUsers();
      setUsers(list);
      setError(null);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  return (
    <Panel
      title="Users"
      rightSlot={
        <button
          type="button"
          onClick={() => setShowInvite(true)}
          className="min-h-[44px] md:min-h-0 md:h-7 px-2 text-[9px] uppercase tracking-wide bg-bg-elevated hover:bg-bg-base border border-border rounded"
        >
          + Invite
        </button>
      }
    >
      {error !== null && (
        <div role="alert" className="text-red text-[10px] mb-2">
          {error}
        </div>
      )}
      {users === null ? (
        <div className="text-text-tertiary">Loading…</div>
      ) : users.length === 0 ? (
        <div className="text-text-tertiary">No users.</div>
      ) : (
        <table
          aria-label="Users"
          className="w-full text-[10px] font-mono border-collapse"
        >
          <thead>
            <tr className="text-text-tertiary text-left uppercase tracking-wide">
              <th className="py-1 pr-2">Email</th>
              <th className="py-1 pr-2">Last Login</th>
              <th className="py-1 pr-2">Status</th>
              <th className="py-1 pr-2">Mode</th>
              <th className="py-1 pr-2">Role</th>
              <th className="py-1 text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr
                key={u.id}
                data-testid={`user-row-${u.id}`}
                className="border-t border-border"
              >
                <td className="py-1 pr-2">{u.email}</td>
                <td className="py-1 pr-2 text-text-secondary">
                  {fmtTs(u.last_login)}
                </td>
                <td className="py-1 pr-2">
                  <StatusPill status={statusOf(u)} />
                </td>
                <td className="py-1 pr-2 text-text-secondary">
                  {u.trading_mode}
                </td>
                <td className="py-1 pr-2">
                  {u.is_admin ? (
                    <span
                      data-testid="admin-badge"
                      className="inline-block px-1.5 py-0.5 rounded text-[8px] uppercase tracking-wide bg-gold/15 text-gold border border-gold/30"
                    >
                      Admin
                    </span>
                  ) : (
                    <span className="text-text-tertiary">—</span>
                  )}
                </td>
                <td className="py-1 text-right">
                  <UserRowMenu user={u} onChanged={reload} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {showInvite && (
        <InviteUserModal
          onClose={() => setShowInvite(false)}
          onCreated={() => {
            setShowInvite(false);
            void reload();
          }}
        />
      )}
    </Panel>
  );
}
