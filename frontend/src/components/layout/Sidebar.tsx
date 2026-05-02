import type { PropsWithChildren } from "react";

interface SidebarProps {
  open: boolean;
  onClose: () => void;
}

export function Sidebar({ open, onClose, children }: PropsWithChildren<SidebarProps>) {
  return (
    <>
      {/* Mobile backdrop */}
      <div
        className={`md:hidden fixed inset-0 bg-black/50 transition-opacity z-40 ${
          open ? "opacity-100" : "opacity-0 pointer-events-none"
        }`}
        onClick={onClose}
        aria-hidden
      />
      <aside
        className={`fixed md:static z-50 top-0 right-0 h-full w-[260px] md:w-[230px]
          bg-bg-base border-l border-border overflow-y-auto p-1
          transition-transform md:translate-x-0
          ${open ? "translate-x-0" : "translate-x-full"}`}
      >
        <button
          type="button"
          onClick={onClose}
          className="md:hidden h-11 w-full text-right pr-2 text-text-secondary"
          aria-label="Close sidebar"
        >
          ✕
        </button>
        {children}
      </aside>
    </>
  );
}
