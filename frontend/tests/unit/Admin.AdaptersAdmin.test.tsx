import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { AdaptersAdmin } from "@/tabs/Admin/AdaptersAdmin";
import type { AdapterHealth, SyncResult } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  api: {
    adminListAdapters: vi.fn(),
    adminSyncAdapter: vi.fn(),
  },
}));

import { api } from "@/lib/api";
const mockedList = vi.mocked(api.adminListAdapters);
const mockedSync = vi.mocked(api.adminSyncAdapter);

function adp(overrides: Partial<AdapterHealth> = {}): AdapterHealth {
  return {
    exchange: "binance",
    checked_at: "2026-05-04T12:00:00Z",
    is_healthy: true,
    latency_ms: 12,
    error_message: null,
    quota_used_pct: null,
    ...overrides,
  };
}

beforeEach(() => {
  mockedList.mockReset();
  mockedSync.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("Admin/AdaptersAdmin page", () => {
  test("renders 4 rows for binance / bybit / yahoo / twelvedata", async () => {
    mockedList.mockResolvedValueOnce([
      adp({ exchange: "binance" }),
      adp({ exchange: "bybit" }),
      adp({ exchange: "yahoo" }),
      adp({ exchange: "twelvedata" }),
    ]);
    render(<AdaptersAdmin />);
    await waitFor(() => {
      expect(screen.getByText("binance")).toBeInTheDocument();
    });
    expect(screen.getByText("bybit")).toBeInTheDocument();
    expect(screen.getByText("yahoo")).toBeInTheDocument();
    expect(screen.getByText("twelvedata")).toBeInTheDocument();
    expect(screen.getAllByTestId("adapter-row")).toHaveLength(4);
  });

  test("health pills reflect is_healthy", async () => {
    mockedList.mockResolvedValueOnce([
      adp({ exchange: "binance", is_healthy: true }),
      adp({ exchange: "bybit", is_healthy: false, error_message: "timeout" }),
    ]);
    render(<AdaptersAdmin />);
    await waitFor(() => {
      expect(screen.getByText("binance")).toBeInTheDocument();
    });
    const pills = screen.getAllByTestId("adapter-health-pill");
    expect(pills[0]).toHaveTextContent(/healthy/i);
    expect(pills[1]).toHaveTextContent(/unhealthy/i);
  });

  test("latency_ms rendered as '12 ms' or '—' if null", async () => {
    mockedList.mockResolvedValueOnce([
      adp({ exchange: "binance", latency_ms: 12 }),
      adp({ exchange: "bybit", latency_ms: null }),
    ]);
    render(<AdaptersAdmin />);
    await waitFor(() => {
      expect(screen.getByText("binance")).toBeInTheDocument();
    });
    expect(screen.getByText("12 ms")).toBeInTheDocument();
    // The bybit row should contain an em-dash for latency.
    const bybitRow = screen.getByText("bybit").closest("tr");
    expect(bybitRow).not.toBeNull();
    expect(bybitRow!.textContent).toContain("—");
  });

  test("Sync button calls adminSyncAdapter and reloads list, displays SyncResult", async () => {
    mockedList.mockResolvedValueOnce([adp({ exchange: "binance" })]);
    const result: SyncResult = {
      exchange: "binance",
      added: 3,
      still_active: 200,
      newly_delisted: 1,
    };
    mockedSync.mockResolvedValueOnce(result);
    mockedList.mockResolvedValueOnce([adp({ exchange: "binance" })]);

    render(<AdaptersAdmin />);
    await waitFor(() => {
      expect(screen.getByText("binance")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /sync/i }));
    await waitFor(() => {
      expect(mockedSync).toHaveBeenCalledWith("binance");
    });
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledTimes(2);
    });
    await waitFor(() => {
      expect(screen.getByTestId("adapter-sync-result-binance")).toHaveTextContent(
        /added 3/i,
      );
    });
    expect(
      screen.getByTestId("adapter-sync-result-binance"),
    ).toHaveTextContent(/still 200/i);
    expect(
      screen.getByTestId("adapter-sync-result-binance"),
    ).toHaveTextContent(/removed 1/i);
  });

  test("renders empty state when no adapters", async () => {
    mockedList.mockResolvedValueOnce([]);
    render(<AdaptersAdmin />);
    await waitFor(() => {
      expect(screen.getByText(/no adapters/i)).toBeInTheDocument();
    });
  });

  test("renders error when API fails", async () => {
    mockedList.mockRejectedValueOnce(
      new Error("HTTP 500 for /admin/adapters/health"),
    );
    render(<AdaptersAdmin />);
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/HTTP 500/);
    });
  });
});
