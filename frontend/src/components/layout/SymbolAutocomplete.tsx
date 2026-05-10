/**
 * SP-9 Phase A — typeahead symbol picker for the TopNav.
 *
 * As the user types, fires a debounced GET /bot-status/symbol-search
 * and renders matching pairs in a dropdown. Keyboard nav: ↑/↓ to
 * highlight, Enter to commit, Esc to close. Click commits too.
 *
 * The component is uncontrolled re: the *current* symbol — the parent
 * still owns it. We just notify via onSelect when the user picks one.
 */
import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";
import { api, type SymbolSearchHit } from "@/lib/api";

interface Props {
  initialValue: string;
  onSelect: (symbol: string) => void;
  placeholder?: string;
  ariaLabel?: string;
}

const DEBOUNCE_MS = 180;

function formatVolume(v: number | null): string {
  if (v == null) return "";
  if (v >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
  if (v >= 1e3) return `$${(v / 1e3).toFixed(0)}K`;
  return `$${v.toFixed(0)}`;
}

export function SymbolAutocomplete({
  initialValue,
  onSelect,
  placeholder = "search e.g. shiba",
  ariaLabel = "Symbol",
}: Props) {
  const [draft, setDraft] = useState(initialValue);
  const [hits, setHits] = useState<SymbolSearchHit[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [loading, setLoading] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const reqIdRef = useRef(0);
  // Last value committed via onSelect — used to suppress wasteful
  // re-fetches when the input re-focuses after a selection.
  const lastCommittedRef = useRef(initialValue);
  const listboxId = useId();

  // Re-sync draft when parent forces a different symbol from outside
  // (e.g. clicking a row in another panel, or selecting from another tab).
  useEffect(() => {
    setDraft(initialValue);
    lastCommittedRef.current = initialValue;
  }, [initialValue]);

  // Click-outside closes the dropdown.
  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (
        wrapRef.current &&
        !wrapRef.current.contains(e.target as Node)
      ) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  // Debounced search. Sequence-numbered to discard out-of-order responses.
  useEffect(() => {
    if (!open || draft.length < 1) {
      setHits([]);
      return;
    }
    // Skip refetching when the draft equals the last committed value.
    // Without this, re-focusing the input after a selection re-fired
    // a search of the just-confirmed symbol on every focus.
    if (draft === lastCommittedRef.current) {
      return;
    }
    const myReqId = ++reqIdRef.current;
    setLoading(true);
    const t = setTimeout(() => {
      api.symbolSearch(draft, { limit: 10 })
        .then((r) => {
          if (reqIdRef.current === myReqId) {
            setHits(r.hits);
            setActive(0);
          }
        })
        .catch(() => {
          if (reqIdRef.current === myReqId) setHits([]);
        })
        .finally(() => {
          if (reqIdRef.current === myReqId) setLoading(false);
        });
    }, DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [draft, open]);

  const commit = useCallback(
    (sym: string) => {
      setDraft(sym);
      lastCommittedRef.current = sym;
      setOpen(false);
      // Drop focus so the next click on the input is a fresh interaction
      // (and doesn't immediately re-open + re-search).
      inputRef.current?.blur();
      onSelect(sym);
    },
    [onSelect],
  );

  function onKey(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (!open) setOpen(true);
      if (hits.length > 0) {
        setActive((i) => Math.min(i + 1, hits.length - 1));
      }
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (hits.length > 0) {
        setActive((i) => Math.max(i - 1, 0));
      }
    } else if (e.key === "Enter") {
      e.preventDefault();
      const pick = hits[active];
      if (pick) {
        commit(pick.symbol);
      } else if (loading) {
        // Don't commit while debounce / fetch is still resolving — the
        // user might be expecting suggestions. Wait one tick and let
        // the next render either show hits OR fall through to the
        // "no matches" branch below.
        return;
      } else if (draft.trim()) {
        // No suggestions arrived. Hand the parent the typed value;
        // App.handleSymbolChange normalises slashless input
        // (SHIB -> SHIB/USDT) before passing to the chart.
        commit(draft.trim().toUpperCase());
      }
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  }

  const showDropdown = open && (loading || hits.length > 0);
  const activeHit = hits[active];
  const activeId = useMemo(
    () => (showDropdown && activeHit ? `${listboxId}-opt-${active}` : undefined),
    [showDropdown, activeHit, listboxId, active],
  );

  return (
    <div ref={wrapRef} className="relative">
      <input
        ref={inputRef}
        type="text"
        value={draft}
        onChange={(e) => {
          setDraft(e.target.value.toUpperCase());
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKey}
        className="bg-bg-base border border-border rounded px-2 py-1 text-[10px] font-mono w-32"
        aria-label={ariaLabel}
        placeholder={placeholder}
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={showDropdown}
        aria-controls={listboxId}
        {...(activeId ? { "aria-activedescendant": activeId } : {})}
        autoComplete="off"
      />
      {showDropdown ? (
        <ul
          id={listboxId}
          role="listbox"
          aria-label="Symbol suggestions"
          className="absolute right-0 top-full mt-1 z-50 w-56 bg-bg-elevated border border-border rounded shadow-lg max-h-72 overflow-auto"
        >
          {loading && hits.length === 0 ? (
            <li className="px-2 py-1 text-[10px] text-text-tertiary">
              searching…
            </li>
          ) : null}
          {hits.map((h, i) => (
            <li
              key={`${h.exchange}:${h.symbol}`}
              id={`${listboxId}-opt-${i}`}
              role="option"
              aria-selected={i === active}
              onMouseDown={(e) => {
                e.preventDefault();
                commit(h.symbol);
              }}
              onTouchStart={(e) => {
                e.preventDefault();
                commit(h.symbol);
              }}
              onMouseEnter={() => setActive(i)}
              className={[
                "flex items-center justify-between gap-2 px-2 py-1 cursor-pointer text-[10px] font-mono",
                i === active
                  ? "bg-bg-panel text-text-primary"
                  : "text-text-secondary hover:bg-bg-panel",
              ].join(" ")}
            >
              <span>{h.symbol}</span>
              <span className="text-text-tertiary text-[8.5px]">
                {h.exchange}
                {h.quote_volume_24h_usdt !== null
                  ? ` · ${formatVolume(h.quote_volume_24h_usdt)}`
                  : ""}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
