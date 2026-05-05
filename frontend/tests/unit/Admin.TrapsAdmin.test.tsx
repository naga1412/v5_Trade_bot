import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { TrapsAdmin } from "@/tabs/Admin/TrapsAdmin";
import type { TrapEntry } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  api: {
    adminListTraps: vi.fn(),
    adminToggleTrap: vi.fn(),
  },
}));

import { api } from "@/lib/api";
const mockedList = vi.mocked(api.adminListTraps);
const mockedToggle = vi.mocked(api.adminToggleTrap);

function tr(overrides: Partial<TrapEntry> = {}): TrapEntry {
  return {
    trap_id: "liquidity_sweep",
    severity: "medium",
    side: "both",
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

describe("Admin/TrapsAdmin page", () => {
  test("renders rows with trap_id + severity + side + status pill", async () => {
    mockedList.mockResolvedValueOnce([
      tr({ trap_id: "liquidity_sweep", severity: "high", side: "long" }),
      tr({ trap_id: "stop_hunt", severity: "extreme", side: "short" }),
    ]);
    render(<TrapsAdmin />);
    await waitFor(() => {
      expect(screen.getByText("liquidity_sweep")).toBeInTheDocument();
    });
    expect(screen.getByText("stop_hunt")).toBeInTheDocument();
    expect(screen.getAllByTestId("trap-status-pill")).toHaveLength(2);
    expect(screen.getAllByTestId("trap-severity-pill")).toHaveLength(2);
  });

  test("severity pill has correct color class for medium/high/extreme", async () => {
    mockedList.mockResolvedValueOnce([
      tr({ trap_id: "a", severity: "medium" }),
      tr({ trap_id: "b", severity: "high" }),
      tr({ trap_id: "c", severity: "extreme" }),
    ]);
    render(<TrapsAdmin />);
    await waitFor(() => {
      expect(screen.getByText("a")).toBeInTheDocument();
    });
    const pills = screen.getAllByTestId("trap-severity-pill");
    expect(pills[0]?.className).toMatch(/yellow/);
    expect(pills[1]?.className).toMatch(/orange/);
    expect(pills[2]?.className).toMatch(/red/);
  });

  test("clicking Disable calls adminToggleTrap with reason from window.prompt", async () => {
    mockedList.mockResolvedValueOnce([
      tr({ trap_id: "liquidity_sweep", enabled: true }),
    ]);
    mockedToggle.mockResolvedValueOnce(
      tr({ trap_id: "liquidity_sweep", enabled: false }),
    );
    mockedList.mockResolvedValueOnce([
      tr({ trap_id: "liquidity_sweep", enabled: false }),
    ]);

    const promptSpy = vi.spyOn(window, "prompt").mockReturnValue("noisy");

    render(<TrapsAdmin />);
    await waitFor(() => {
      expect(screen.getByText("liquidity_sweep")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /disable/i }));
    await waitFor(() => {
      expect(mockedToggle).toHaveBeenCalledWith(
        "liquidity_sweep",
        false,
        "noisy",
      );
    });
    promptSpy.mockRestore();
  });

  test("clicking Enable calls adminToggleTrap with enable=true", async () => {
    mockedList.mockResolvedValueOnce([
      tr({ trap_id: "liquidity_sweep", enabled: false }),
    ]);
    mockedToggle.mockResolvedValueOnce(
      tr({ trap_id: "liquidity_sweep", enabled: true }),
    );
    mockedList.mockResolvedValueOnce([
      tr({ trap_id: "liquidity_sweep", enabled: true }),
    ]);

    render(<TrapsAdmin />);
    await waitFor(() => {
      expect(screen.getByText("liquidity_sweep")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /enable/i }));
    await waitFor(() => {
      expect(mockedToggle).toHaveBeenCalledWith(
        "liquidity_sweep",
        true,
        undefined,
      );
    });
  });

  test("renders empty state when no traps", async () => {
    mockedList.mockResolvedValueOnce([]);
    render(<TrapsAdmin />);
    await waitFor(() => {
      expect(screen.getByText(/no traps/i)).toBeInTheDocument();
    });
  });

  test("renders error when API fails", async () => {
    mockedList.mockRejectedValueOnce(new Error("HTTP 500 for /admin/traps"));
    render(<TrapsAdmin />);
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/HTTP 500/);
    });
  });

  test("shows the side label (long/short/both) per row", async () => {
    mockedList.mockResolvedValueOnce([
      tr({ trap_id: "a", side: "long" }),
      tr({ trap_id: "b", side: "short" }),
      tr({ trap_id: "c", side: "both" }),
    ]);
    render(<TrapsAdmin />);
    await waitFor(() => {
      expect(screen.getByText("a")).toBeInTheDocument();
    });
    const aRow = screen.getByText("a").closest("tr");
    const bRow = screen.getByText("b").closest("tr");
    const cRow = screen.getByText("c").closest("tr");
    expect(aRow?.textContent).toContain("long");
    expect(bRow?.textContent).toContain("short");
    expect(cRow?.textContent).toContain("both");
  });
});
