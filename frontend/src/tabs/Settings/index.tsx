import { useState } from "react";
import { Profile } from "@/tabs/Settings/Profile";
import { Trading } from "@/tabs/Settings/Trading";
import { Secrets } from "@/tabs/Settings/Secrets";

type SubTab = "profile" | "trading" | "secrets";

const SUB_TABS: readonly { id: SubTab; label: string }[] = [
  { id: "profile", label: "Profile" },
  { id: "trading", label: "Trading" },
  { id: "secrets", label: "Secrets" },
];

// SP-0.7 Phase K: Settings tab assembles three sub-pages with internal nav.
// The hash router only manages the top-level tab; sub-tab is local state so
// going back to Settings from another tab lands on the same sub-tab.
export function Settings() {
  const [sub, setSub] = useState<SubTab>("profile");
  return (
    <div className="h-full flex flex-col overflow-hidden">
      <div
        role="tablist"
        aria-label="Settings sections"
        className="flex bg-bg-elevated border-b border-border"
      >
        {SUB_TABS.map((t) => {
          const active = t.id === sub;
          return (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={active}
              data-active={active ? "true" : "false"}
              onClick={() => setSub(t.id)}
              className={[
                "h-11 md:h-9 px-4 text-xs font-mono uppercase tracking-wide",
                "border-b-2 -mb-px transition-colors",
                active
                  ? "text-text-primary border-text-primary bg-bg-base"
                  : "text-text-secondary border-transparent hover:text-text-primary",
              ].join(" ")}
            >
              {t.label}
            </button>
          );
        })}
      </div>
      <div className="flex-1 min-h-0 overflow-auto p-3">
        {sub === "profile" ? <Profile /> :
         sub === "trading" ? <Trading /> :
         <Secrets />}
      </div>
    </div>
  );
}
