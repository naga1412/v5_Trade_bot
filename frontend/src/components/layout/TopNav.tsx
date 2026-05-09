import { SymbolAutocomplete } from "./SymbolAutocomplete";

interface TopNavProps {
  symbol: string;
  onSymbolChange: (s: string) => void;
  onMenuClick: () => void;
}

export function TopNav({ symbol, onSymbolChange, onMenuClick }: TopNavProps) {
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
      <div className="ml-auto flex items-center gap-2">
        <SymbolAutocomplete
          initialValue={symbol}
          onSelect={onSymbolChange}
        />
      </div>
    </nav>
  );
}
