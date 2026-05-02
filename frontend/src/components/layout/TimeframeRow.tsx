const TFS = ["1m", "5m", "15m", "1h", "4h", "1d"] as const;
type Tf = typeof TFS[number];

interface Props {
  active: Tf;
  onChange: (tf: Tf) => void;
}

export function TimeframeRow({ active, onChange }: Props) {
  return (
    <div className="h-7 bg-bg-base border-b border-border flex items-center px-2 gap-1 overflow-x-auto">
      {TFS.map((tf) => (
        <button
          key={tf}
          type="button"
          onClick={() => onChange(tf)}
          className={`min-h-11 min-w-11 px-2 text-[10px] font-mono rounded ${
            active === tf
              ? "bg-purple text-bg-base"
              : "text-text-secondary hover:bg-bg-elevated"
          }`}
        >
          {tf}
        </button>
      ))}
    </div>
  );
}
