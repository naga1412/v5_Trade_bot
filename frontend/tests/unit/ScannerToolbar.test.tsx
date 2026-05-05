import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import type { ScannerFilterCounts } from "@/lib/api";
import { ScannerToolbar } from "@/tabs/Tab3Scanner/ScannerToolbar";

const counts: ScannerFilterCounts = {
  all: 50,
  confirmed: 4,
  probable: 8,
  weak: 30,
  diverging: 8,
};

interface Handlers {
  onMarketChange?: ReturnType<typeof vi.fn>;
  onTfChange?: ReturnType<typeof vi.fn>;
  onAssetCountChange?: ReturnType<typeof vi.fn>;
  onRefreshMinChange?: ReturnType<typeof vi.fn>;
  onSearchChange?: ReturnType<typeof vi.fn>;
  onWatchlistAdd?: ReturnType<typeof vi.fn>;
  onSortChange?: ReturnType<typeof vi.fn>;
  onFilterChange?: ReturnType<typeof vi.fn>;
  onRefresh?: ReturnType<typeof vi.fn>;
}

function renderToolbar(handlers: Handlers = {}) {
  return render(
    <ScannerToolbar
      market="crypto"
      onMarketChange={handlers.onMarketChange ?? vi.fn()}
      tf="1h"
      onTfChange={handlers.onTfChange ?? vi.fn()}
      assetCount={50}
      onAssetCountChange={handlers.onAssetCountChange ?? vi.fn()}
      refreshMin={2}
      onRefreshMinChange={handlers.onRefreshMinChange ?? vi.fn()}
      search=""
      onSearchChange={handlers.onSearchChange ?? vi.fn()}
      watchlist=""
      onWatchlistChange={vi.fn()}
      onWatchlistAdd={handlers.onWatchlistAdd ?? vi.fn()}
      sort="ai_score"
      onSortChange={handlers.onSortChange ?? vi.fn()}
      activeFilter="all"
      onFilterChange={handlers.onFilterChange ?? vi.fn()}
      filterCounts={counts}
      onRefresh={handlers.onRefresh ?? vi.fn()}
    />,
  );
}

describe("ScannerToolbar", () => {
  test("renders all controls", () => {
    renderToolbar();
    expect(screen.getByLabelText(/search assets/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/watchlist/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Add/ })).toBeInTheDocument();
    expect(screen.getByLabelText(/^market$/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/timeframe/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/asset count/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/refresh minutes/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/^sort$/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Refresh/i })).toBeInTheDocument();
  });

  test("typing in search fires onSearchChange", () => {
    const onSearchChange = vi.fn();
    renderToolbar({ onSearchChange });
    fireEvent.change(screen.getByLabelText(/search assets/i), {
      target: { value: "BTC" },
    });
    expect(onSearchChange).toHaveBeenCalledWith("BTC");
  });

  test("changing tf select fires onTfChange", () => {
    const onTfChange = vi.fn();
    renderToolbar({ onTfChange });
    fireEvent.change(screen.getByLabelText(/timeframe/i), {
      target: { value: "4h" },
    });
    expect(onTfChange).toHaveBeenCalledWith("4h");
  });

  test("changing asset count fires onAssetCountChange with a number", () => {
    const onAssetCountChange = vi.fn();
    renderToolbar({ onAssetCountChange });
    fireEvent.change(screen.getByLabelText(/asset count/i), {
      target: { value: "100" },
    });
    expect(onAssetCountChange).toHaveBeenCalledWith(100);
  });

  test("changing refresh minutes fires onRefreshMinChange with a number", () => {
    const onRefreshMinChange = vi.fn();
    renderToolbar({ onRefreshMinChange });
    fireEvent.change(screen.getByLabelText(/refresh minutes/i), {
      target: { value: "5" },
    });
    expect(onRefreshMinChange).toHaveBeenCalledWith(5);
  });

  test("clicking refresh button fires onRefresh", () => {
    const onRefresh = vi.fn();
    renderToolbar({ onRefresh });
    fireEvent.click(screen.getByRole("button", { name: /Refresh/i }));
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  test("clicking ★ Add fires onWatchlistAdd", () => {
    const onWatchlistAdd = vi.fn();
    renderToolbar({ onWatchlistAdd });
    fireEvent.click(screen.getByRole("button", { name: /Add/ }));
    expect(onWatchlistAdd).toHaveBeenCalledTimes(1);
  });

  test("renders FilterPills with provided counts", () => {
    renderToolbar();
    expect(screen.getByRole("button", { name: /All 50/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Confirmed 4/ })).toBeInTheDocument();
  });

  test("filter pill clicks bubble to onFilterChange", () => {
    const onFilterChange = vi.fn();
    renderToolbar({ onFilterChange });
    fireEvent.click(screen.getByRole("button", { name: /Probable/ }));
    expect(onFilterChange).toHaveBeenCalledWith("probable");
  });
});
