import { useState } from "react";
import { Users } from "@/tabs/Admin/Users";
import { AuditTrail } from "@/tabs/Admin/AuditTrail";

type SubTab = "users" | "audit";

const SUB_TABS: readonly { id: SubTab; label: string }[] = [
  { id: "users", label: "Users" },
  { id: "audit", label: "Audit Trail" },
];

// SP-0.7 Phase J: Admin tab assembles Users + AuditTrail sub-pages.
export function Admin() {
  const [sub, setSub] = useState<SubTab>("users");
  return (
    <div className="h-full flex flex-col overflow-hidden">
      <div
        role="tablist"
        aria-label="Admin sections"
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
        {sub === "users" ? <Users /> : <AuditTrail />}
      </div>
    </div>
  );
}
