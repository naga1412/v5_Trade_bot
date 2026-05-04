import type { TabId } from "@/lib/useHashRoute";

interface TabNavProps {
  active: TabId;
  onChange: (id: TabId) => void;
}

interface TabDef {
  id: TabId;
  label: string;
}

// Order matters: spec §9.1 puts Live Prediction first, Bot Status second.
const TABS: readonly TabDef[] = [
  { id: "live-prediction", label: "Live Prediction" },
  { id: "bot-status", label: "Bot Status" },
];

export function TabNav({ active, onChange }: TabNavProps) {
  return (
    <div
      role="tablist"
      aria-label="Primary"
      className="flex bg-bg-elevated border-b border-border"
    >
      {TABS.map((t) => {
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
