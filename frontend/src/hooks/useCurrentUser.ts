import { useEffect, useSyncExternalStore } from "react";
import { api, type MeOut } from "@/lib/api";

// SP-0.7 Phase I (task I1): shared module-scope cache for /api/v1/me. Many
// components (App, ImpersonationBanner, TabNav, Settings, Admin) need the
// current user — fetching it once and broadcasting via useSyncExternalStore
// avoids duplicate REST calls and keeps every consumer in sync.

export interface CurrentUserState {
  user: MeOut | null;
  isLoading: boolean;
  error: Error | null;
}

const INITIAL: CurrentUserState = {
  user: null,
  isLoading: true,
  error: null,
};

let state: CurrentUserState = INITIAL;
let inFlight: Promise<void> | null = null;
const listeners = new Set<() => void>();

function setState(next: CurrentUserState): void {
  state = next;
  for (const l of listeners) l();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function getSnapshot(): CurrentUserState {
  return state;
}

async function loadOnce(): Promise<void> {
  if (inFlight) return inFlight;
  inFlight = (async () => {
    setState({ user: state.user, isLoading: true, error: null });
    try {
      const me = await api.me();
      setState({ user: me, isLoading: false, error: null });
    } catch (e) {
      const err = e instanceof Error ? e : new Error(String(e));
      setState({ user: null, isLoading: false, error: err });
    } finally {
      inFlight = null;
    }
  })();
  return inFlight;
}

export interface UseCurrentUserResult {
  user: MeOut | null;
  isAdmin: boolean;
  isImpersonating: boolean;
  isLoading: boolean;
  error: Error | null;
  reload: () => Promise<void>;
}

export function useCurrentUser(): UseCurrentUserResult {
  const snap = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);

  useEffect(() => {
    // First subscriber triggers the fetch. Subsequent mounts piggy-back on the
    // shared cache. `loadOnce` is idempotent.
    if (snap.user === null && snap.error === null && !inFlight) {
      void loadOnce();
    }
    // We deliberately depend on nothing — each consumer's mount only needs to
    // trigger the load once when no fetch is in flight and no data exists.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return {
    user: snap.user,
    isAdmin: snap.user?.is_admin === true,
    isImpersonating: snap.user?.is_impersonating === true,
    isLoading: snap.isLoading,
    error: snap.error,
    reload: loadOnce,
  };
}

// Test-only: reset module-scope cache between tests.
export function __resetCurrentUserForTests(): void {
  state = INITIAL;
  inFlight = null;
  listeners.clear();
}
