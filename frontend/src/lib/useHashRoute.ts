import { useEffect, useState, useCallback } from "react";

// SP-0.5 spec §9.1: URL-driven tab state. We avoid pulling in react-router-dom
// for a two-tab nav. The hash format is "#/<tab-id>". Anything else falls back
// to the default tab.

export type TabId = "live-prediction" | "bot-status";

const VALID: ReadonlySet<TabId> = new Set<TabId>(["live-prediction", "bot-status"]);

function parseHash(hash: string, fallback: TabId): TabId {
  // Strip leading "#" then leading "/" — accept both "#/bot-status" and "#bot-status".
  const stripped = hash.replace(/^#/, "").replace(/^\//, "");
  return (VALID as ReadonlySet<string>).has(stripped) ? (stripped as TabId) : fallback;
}

export function useHashRoute(defaultTab: TabId = "live-prediction"): {
  tab: TabId;
  setTab: (id: TabId) => void;
} {
  const [tab, setTabState] = useState<TabId>(() =>
    typeof window === "undefined" ? defaultTab : parseHash(window.location.hash, defaultTab),
  );

  useEffect(() => {
    const onHashChange = (): void => {
      setTabState(parseHash(window.location.hash, defaultTab));
    };
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, [defaultTab]);

  const setTab = useCallback((id: TabId) => {
    // Update state synchronously and then write the hash. The hashchange
    // listener will fire too (as a no-op resync) — this avoids relying on
    // JSDOM's async hashchange dispatch in tests, and feels snappier in real
    // browsers where the listener would otherwise lag a frame behind.
    setTabState(id);
    if (window.location.hash !== "#/" + id) {
      window.location.hash = "#/" + id;
    }
  }, []);

  return { tab, setTab };
}
