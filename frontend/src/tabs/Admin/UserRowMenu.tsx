import { useEffect, useRef, useState } from "react";
import { api, type AdminUser } from "@/lib/api";

interface UserRowMenuProps {
  user: AdminUser;
  onChanged: () => void | Promise<void>;
}

// SP-0.7 J3: per-row gear (cogwheel) menu with View as user / Toggle is_active
// / Toggle is_admin / Delete (soft). Each action calls the corresponding API
// then invokes onChanged() so the parent can refetch. Impersonate (J4)
// triggers a full window.location.reload() so useCurrentUser refetches and
// the banner appears.
export function UserRowMenu({ user, onChanged }: UserRowMenuProps) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent): void => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const run = async (fn: () => Promise<unknown>): Promise<void> => {
    if (busy) return;
    setBusy(true);
    try {
      await fn();
    } finally {
      setBusy(false);
      setOpen(false);
    }
  };

  const onImpersonate = (): Promise<void> =>
    run(async () => {
      await api.adminImpersonate(user.id);
      // J4: full reload so useCurrentUser refetches the now-impersonated /me
      // and every cached query in the app picks up the new effective user.
      window.location.reload();
    });

  const onToggleActive = (): Promise<void> =>
    run(async () => {
      await api.adminPatchUser(user.id, { is_active: !user.is_active });
      await onChanged();
    });

  const onToggleAdmin = (): Promise<void> =>
    run(async () => {
      await api.adminPatchUser(user.id, { is_admin: !user.is_admin });
      await onChanged();
    });

  const onDelete = (): Promise<void> =>
    run(async () => {
      await api.adminDeleteUser(user.id);
      await onChanged();
    });

  return (
    <div ref={ref} className="relative inline-block text-left">
      <button
        type="button"
        aria-label={`Actions for ${user.email}`}
        aria-haspopup="menu"
        aria-expanded={open}
        data-testid={`row-menu-trigger-${user.id}`}
        onClick={() => setOpen((v) => !v)}
        className="min-h-[44px] md:min-h-0 md:h-7 w-7 inline-flex items-center justify-center rounded hover:bg-bg-elevated"
      >
        <span aria-hidden="true">⚙</span>
      </button>
      {open && (
        <div
          role="menu"
          data-testid={`row-menu-${user.id}`}
          className="absolute right-0 z-10 mt-1 min-w-[180px] bg-bg-panel border border-border rounded shadow-lg text-[10px]"
        >
          <button
            type="button"
            role="menuitem"
            onClick={onImpersonate}
            disabled={busy}
            className="block w-full text-left px-3 py-2 hover:bg-bg-elevated disabled:opacity-50"
          >
            View as user
          </button>
          <button
            type="button"
            role="menuitem"
            onClick={onToggleActive}
            disabled={busy}
            className="block w-full text-left px-3 py-2 hover:bg-bg-elevated disabled:opacity-50"
          >
            {user.is_active ? "Deactivate" : "Reactivate"}
          </button>
          <button
            type="button"
            role="menuitem"
            onClick={onToggleAdmin}
            disabled={busy}
            className="block w-full text-left px-3 py-2 hover:bg-bg-elevated disabled:opacity-50"
          >
            {user.is_admin ? "Demote from admin" : "Promote to admin"}
          </button>
          <button
            type="button"
            role="menuitem"
            onClick={onDelete}
            disabled={busy}
            className="block w-full text-left px-3 py-2 text-red hover:bg-red/15 disabled:opacity-50"
          >
            Delete (soft)
          </button>
        </div>
      )}
    </div>
  );
}
