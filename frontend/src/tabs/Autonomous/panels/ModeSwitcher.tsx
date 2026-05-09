/**
 * SP-8 Phase I — mode switcher panel.
 *
 * Three buttons (Manual / Telegram-approve / Fully-auto). Locked
 * states show a 🔒 icon when promotion gates haven't passed.
 *
 * Phase J wiring: GET /api/v1/me/trading-mode + PATCH for change,
 * with hardware-confirm modal on upgrade.
 */
import { Panel } from "@/components/ui/Panel";

type Mode = "manual" | "telegram-approve" | "fully-auto";

const MODES: { id: Mode; label: string; locked?: boolean }[] = [
  { id: "manual", label: "Manual" },
  { id: "telegram-approve", label: "Telegram approve", locked: true },
  { id: "fully-auto", label: "Fully auto", locked: true },
];

export function ModeSwitcher() {
  // Phase J: read user.trading_mode from /api/v1/me; Phase J also
  // consumes GateStatus's snapshot to set `locked` per-mode.
  const currentMode: Mode = "manual";

  return (
    <Panel title="Trading Mode">
      <div className="flex gap-1">
        {MODES.map((m) => {
          const isActive = m.id === currentMode;
          return (
            <button
              key={m.id}
              type="button"
              disabled={m.locked && !isActive}
              className={[
                "flex-1 px-3 py-2 text-[10px] uppercase tracking-wide rounded",
                "border transition-colors",
                isActive
                  ? "bg-bg-panel border-text-primary text-text-primary"
                  : m.locked
                    ? "bg-bg-base border-border text-text-tertiary cursor-not-allowed"
                    : "bg-bg-base border-border text-text-secondary hover:text-text-primary",
              ].join(" ")}
            >
              {m.locked && !isActive ? "🔒 " : ""}
              {m.label}
            </button>
          );
        })}
      </div>
      <p className="text-[8px] text-text-tertiary mt-1">
        Upgrades require gates passed (see right) + hardware confirm. Downgrades always allowed.
      </p>
    </Panel>
  );
}
