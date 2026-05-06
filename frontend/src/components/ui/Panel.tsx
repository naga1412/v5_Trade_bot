import type { PropsWithChildren } from "react";

interface PanelProps {
  title: string;
  rightSlot?: React.ReactNode;
  intensity?: "default" | "alert";
  // SP-9 Phase F4: appended to the outer <section>'s className so callers
  // can opt into extra border / background tweaks (e.g. HIGH-impact red).
  className?: string;
}

export function Panel({
  title, rightSlot, children, intensity = "default", className = "",
}: PropsWithChildren<PanelProps>) {
  const border =
    intensity === "alert" ? "border border-red/60" : "border border-border";
  return (
    <section
      className={`bg-bg-panel rounded-[4px] ${border} px-[0.55rem] py-[0.4rem] mb-[3px] ${className}`}
    >
      <header className="flex items-center justify-between mb-1">
        <h3 className="text-[7.5px] uppercase tracking-[0.04em] text-text-tertiary">
          {title}
        </h3>
        {rightSlot}
      </header>
      <div className="text-[9px] font-mono">{children}</div>
    </section>
  );
}
