import { useState } from "react";
import { Users } from "@/tabs/Admin/Users";
import { AuditTrail } from "@/tabs/Admin/AuditTrail";
import { MlCheckpoints } from "@/tabs/Admin/MlCheckpoints";
import { PatternsAdmin } from "@/tabs/Admin/PatternsAdmin";
import { AdaptersAdmin } from "@/tabs/Admin/AdaptersAdmin";
import { TrapsAdmin } from "@/tabs/Admin/TrapsAdmin";
import { TestTrade } from "@/tabs/Admin/TestTrade";

type SubTab =
  | "users"
  | "audit"
  | "ml-checkpoints"
  | "patterns"
  | "adapters"
  | "traps"
  | "test-trade";

const SUB_TABS: readonly { id: SubTab; label: string }[] = [
  { id: "users", label: "Users" },
  { id: "audit", label: "Audit Trail" },
  { id: "ml-checkpoints", label: "ML Checkpoints" },
  { id: "patterns", label: "Patterns" },
  { id: "adapters", label: "Adapters" },
  { id: "traps", label: "Traps" },
  { id: "test-trade", label: "Test Trade" },
];

// SP-0.7 Phase J + SP-1 Phase F + SP-6 Phase E: Admin tab —
// Users / AuditTrail / MlCheckpoints / Patterns / Adapters / Traps.
export function Admin() {
  const [sub, setSub] = useState<SubTab>("users");
  return (
    <div className="h-full flex flex-col overflow-hidden">
      <div
        role="tablist"
        aria-label="Admin sections"
        className="flex bg-bg-elevated border-b border-border overflow-x-auto"
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
                "border-b-2 -mb-px transition-colors whitespace-nowrap",
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
        {sub === "users" ? (
          <Users />
        ) : sub === "audit" ? (
          <AuditTrail />
        ) : sub === "ml-checkpoints" ? (
          <MlCheckpoints />
        ) : sub === "patterns" ? (
          <PatternsAdmin />
        ) : sub === "adapters" ? (
          <AdaptersAdmin />
        ) : sub === "traps" ? (
          <TrapsAdmin />
        ) : (
          <TestTrade />
        )}
      </div>
    </div>
  );
}
