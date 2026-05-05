import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { PatternsAdmin } from "@/tabs/Admin/PatternsAdmin";
import type { PatternEntry } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  api: {
    adminListPatterns: vi.fn(),
    adminTogglePattern: vi.fn(),
  },
}));

import { api } from "@/lib/api";
const mockedList = vi.mocked(api.adminListPatterns);
const mockedToggle = vi.mocked(api.adminTogglePattern);

function pat(overrides: Partial<PatternEntry> = {}): PatternEntry {
  return {
    pattern_id: "doji",
    pattern_type: "candle",
    symbol: "*",
    timeframe: "*",
    enabled: true,
    disabled_reason: null,
    ...overrides,
  };
}

beforeEach(() => {
  mockedList.mockReset();
  mockedToggle.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("Admin/PatternsAdmin page", () => {
  test("renders rows with pattern_id + type + status pill", async () => {
    mockedList.mockResolvedValueOnce([
      pat({ pattern_id: "doji", pattern_type: "candle", enabled: true }),
      pat({ pattern_id: "head_shoulders", pattern_type: "chart", enabled: false }),
    ]);
    render(<PatternsAdmin />);
    await waitFor(() => {
      expect(screen.getByText("doji")).toBeInTheDocument();
    });
    expect(screen.getByText("head_shoulders")).toBeInTheDocument();
    expect(screen.getAllByTestId("pattern-status-pill")).toHaveLength(2);
  });

  test("status pills reflect enabled state", async () => {
    mockedList.mockResolvedValueOnce([
      pat({ pattern_id: "a", enabled: true }),
      pat({ pattern_id: "b", enabled: false }),
    ]);
    render(<PatternsAdmin />);
    await waitFor(() => {
      expect(screen.getByText("a")).toBeInTheDocument();
    });
    const pills = screen.getAllByTestId("pattern-status-pill");
    expect(pills[0]).toHaveTextContent(/enabled/i);
    expect(pills[1]).toHaveTextContent(/disabled/i);
  });

  test("clicking Disable calls adminTogglePattern with reason from window.prompt", async () => {
    mockedList.mockResolvedValueOnce([pat({ pattern_id: "doji", enabled: true })]);
    mockedToggle.mockResolvedValueOnce(pat({ pattern_id: "doji", enabled: false }));
    mockedList.mockResolvedValueOnce([pat({ pattern_id: "doji", enabled: false })]);

    const promptSpy = vi.spyOn(window, "prompt").mockReturnValue("too noisy");

    render(<PatternsAdmin />);
    await waitFor(() => {
      expect(screen.getByText("doji")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /disable/i }));
    await waitFor(() => {
      expect(mockedToggle).toHaveBeenCalledWith("doji", false, "too noisy");
    });
    promptSpy.mockRestore();
  });

  test("clicking Enable calls adminTogglePattern with enable=true", async () => {
    mockedList.mockResolvedValueOnce([pat({ pattern_id: "doji", enabled: false })]);
    mockedToggle.mockResolvedValueOnce(pat({ pattern_id: "doji", enabled: true }));
    mockedList.mockResolvedValueOnce([pat({ pattern_id: "doji", enabled: true })]);

    render(<PatternsAdmin />);
    await waitFor(() => {
      expect(screen.getByText("doji")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /enable/i }));
    await waitFor(() => {
      expect(mockedToggle).toHaveBeenCalledWith("doji", true, undefined);
    });
  });

  test("search input filters rows by pattern_id", async () => {
    mockedList.mockResolvedValueOnce([
      pat({ pattern_id: "doji" }),
      pat({ pattern_id: "hammer" }),
      pat({ pattern_id: "head_shoulders" }),
    ]);
    render(<PatternsAdmin />);
    await waitFor(() => {
      expect(screen.getByText("doji")).toBeInTheDocument();
    });
    fireEvent.change(screen.getByLabelText(/search patterns/i), {
      target: { value: "ham" },
    });
    expect(screen.queryByText("doji")).not.toBeInTheDocument();
    expect(screen.getByText("hammer")).toBeInTheDocument();
    expect(screen.queryByText("head_shoulders")).not.toBeInTheDocument();
  });

  test("renders empty state when API returns []", async () => {
    mockedList.mockResolvedValueOnce([]);
    render(<PatternsAdmin />);
    await waitFor(() => {
      expect(screen.getByText(/no patterns/i)).toBeInTheDocument();
    });
  });

  test("renders error when API fails", async () => {
    mockedList.mockRejectedValueOnce(new Error("HTTP 500 for /admin/patterns"));
    render(<PatternsAdmin />);
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/HTTP 500/);
    });
  });

  test("paginates: 60 rows -> page 1 shows 50, Next shows 10", async () => {
    const items: PatternEntry[] = [];
    for (let i = 0; i < 60; i++) {
      items.push(pat({ pattern_id: `pat_${String(i).padStart(3, "0")}` }));
    }
    mockedList.mockResolvedValueOnce(items);
    render(<PatternsAdmin />);
    await waitFor(() => {
      expect(screen.getByText("pat_000")).toBeInTheDocument();
    });
    // Page 1 shows pat_000..pat_049
    expect(screen.getByText("pat_049")).toBeInTheDocument();
    expect(screen.queryByText("pat_050")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /next/i }));
    await waitFor(() => {
      expect(screen.getByText("pat_050")).toBeInTheDocument();
    });
    expect(screen.getByText("pat_059")).toBeInTheDocument();
    expect(screen.queryByText("pat_000")).not.toBeInTheDocument();
  });
});
