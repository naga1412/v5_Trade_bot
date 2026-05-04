import { useEffect, useState, useCallback } from "react";

// SP-0.5 spec §9.1 + SP-0.7 Phase I (task I3): URL-driven tab state. We avoid
// pulling in react-router-dom for a four-tab nav. The hash format is
// "#/<tab-id>" optionally followed by "?<query-string>". Anything else falls
// back to the default tab.

export type TabId =
  | "live-prediction"
  | "bot-status"
  | "settings"
  | "admin";

const VALID: ReadonlySet<TabId> = new Set<TabId>([
  "live-prediction",
  "bot-status",
  "settings",
  "admin",
]);

export interface HashRouteState {
  tab: TabId;
  query: Record<string, string>;
  setTab: (id: TabId) => void;
}

interface ParsedHash {
  tab: TabId;
  query: Record<string, string>;
}

function parseHash(hash: string, fallback: TabId): ParsedHash {
  // Strip leading "#" then leading "/" — accept both "#/bot-status" and "#bot-status".
  const stripped = hash.replace(/^#/, "").replace(/^\//, "");
  // Split into "<tab>" and "<query-string>" at the first "?".
  const qIdx = stripped.indexOf("?");
  const tabPart = qIdx === -1 ? stripped : stripped.slice(0, qIdx);
  const queryPart = qIdx === -1 ? "" : stripped.slice(qIdx + 1);

  const tab: TabId = (VALID as ReadonlySet<string>).has(tabPart)
    ? (tabPart as TabId)
    : fallback;

  const query: Record<string, string> = {};
  if (queryPart) {
    const params = new URLSearchParams(queryPart);
    params.forEach((value, key) => {
      query[key] = value;
    });
  }
  return { tab, query };
}

export function useHashRoute(defaultTab: TabId = "live-prediction"): HashRouteState {
  const [state, setState] = useState<ParsedHash>(() =>
    typeof window === "undefined"
      ? { tab: defaultTab, query: {} }
      : parseHash(window.location.hash, defaultTab),
  );

  useEffect(() => {
    const onHashChange = (): void => {
      setState(parseHash(window.location.hash, defaultTab));
    };
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, [defaultTab]);

  const setTab = useCallback((id: TabId) => {
    // Update state synchronously and then write the hash. The hashchange
    // listener will fire too (as a no-op resync) — this avoids relying on
    // JSDOM's async hashchange dispatch in tests, and feels snappier in real
    // browsers where the listener would otherwise lag a frame behind.
    setState((prev) => ({ tab: id, query: prev.query }));
    if (window.location.hash !== "#/" + id) {
      window.location.hash = "#/" + id;
    }
  }, []);

  return { tab: state.tab, query: state.query, setTab };
}
