import { useState } from "react";

interface TopNavProps {
  symbol: string;
  onSymbolChange: (s: string) => void;
  onMenuClick: () => void;
}

export function TopNav({ symbol, onSymbolChange, onMenuClick }: TopNavProps) {
  const [draft, setDraft] = useState(symbol);
  return (
    <nav className="h-8 bg-bg-elevated border-b border-border flex items-center px-2 gap-2">
      <button
        type="button"
        aria-label="Open sidebar"
        onClick={onMenuClick}
        className="md:hidden h-11 w-11 flex items-center justify-center text-text-secondary"
      >
        ≡
      </button>
      <span className="font-mono text-[10px] text-text-secondary">trading-radar</span>
      <form
        className="ml-auto flex items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          onSymbolChange(draft);
        }}
      >
        <input
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value.toUpperCase())}
          className="bg-bg-base border border-border rounded px-2 py-1 text-[10px] font-mono w-32"
          aria-label="Symbol"
        />
      </form>
    </nav>
  );
}
