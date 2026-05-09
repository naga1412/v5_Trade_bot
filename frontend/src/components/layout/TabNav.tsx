import type { TabId } from "@/lib/useHashRoute";

interface TabNavProps {
  active: TabId;
  onChange: (id: TabId) => void;
  /**
   * Whether the Admin tab should be visible. Defaults to false. Settings is
   * always visible (every user has settings) — only Admin is admin-gated.
   */
  adminVisible?: boolean;
}

interface TabDef {
  id: TabId;
  label: string;
}

// Order matters: spec §9.1 + SP-0.7 §I3 + SP-6 Phase A6 — Live Prediction →
// Bot Status → Scanner → Settings → Admin. Settings + Scanner are always
// rendered; Admin is filtered out when adminVisible is false.
const ALL_TABS: readonly TabDef[] = [
  { id: "live-prediction", label: "Live Prediction" },
  { id: "bot-status", label: "Bot Status" },
  { id: "scanner", label: "Scanner" },
  { id: "autonomous", label: "Autonomous" },
  { id: "settings", label: "Settings" },
  { id: "admin", label: "Admin" },
];

export function TabNav({ active, onChange, adminVisible = false }: TabNavProps) {
  const tabs = ALL_TABS.filter((t) => t.id !== "admin" || adminVisible);
  return (
    <div
      role="tablist"
      aria-label="Primary"
      className="flex bg-bg-elevated border-b border-border"
    >
      {tabs.map((t) => {
        const isActive = t.id === active;
        return (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={isActive}
            data-active={isActive ? "true" : "false"}
            onClick={() => onChange(t.id)}
            className={[
              "h-11 flex-1 md:flex-none md:px-4",
              "text-xs font-mono uppercase tracking-wide",
              "border-b-2 -mb-px transition-colors",
              isActive
                ? "text-text-primary border-text-primary bg-bg-base"
                : "text-text-secondary border-transparent hover:text-text-primary",
            ].join(" ")}
          >
            {t.label}
          </button>
        );
      })}
    </div>
  );
}
