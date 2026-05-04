import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { AuditTrail } from "@/tabs/Admin/AuditTrail";
import type { AuditTrailEntry } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  api: {
    adminAuditTrail: vi.fn(),
  },
}));

import { api } from "@/lib/api";
const mockedAudit = vi.mocked(api.adminAuditTrail);

function entrySample(overrides: Partial<AuditTrailEntry> = {}): AuditTrailEntry {
  return {
    table_name: "predictions",
    row_id: 1,
    user_id: 1,
    ts: "2026-05-04T12:00:00Z",
    summary: "BTC/USDT 1h LONG score=0.65",
    ...overrides,
  };
}

beforeEach(() => {
  mockedAudit.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("Admin/AuditTrail", () => {
  test("on mount, calls api.adminAuditTrail and renders rows", async () => {
    mockedAudit.mockResolvedValueOnce([
      entrySample({ row_id: 11 }),
      entrySample({ row_id: 22, table_name: "paper_trades", summary: "ETH SHORT entry=2000" }),
    ]);
    render(<AuditTrail />);
    await waitFor(() => {
      expect(mockedAudit).toHaveBeenCalled();
    });
    const rows = await screen.findAllByTestId("audit-row");
    expect(rows).toHaveLength(2);
    expect(screen.getByText(/BTC\/USDT 1h LONG/)).toBeInTheDocument();
    expect(screen.getByText(/ETH SHORT/)).toBeInTheDocument();
  });

  test("changing the table filter triggers a refetch with table param", async () => {
    mockedAudit.mockResolvedValueOnce([entrySample()]);
    render(<AuditTrail />);
    await waitFor(() => {
      expect(mockedAudit).toHaveBeenCalledTimes(1);
    });
    mockedAudit.mockResolvedValueOnce([]);
    fireEvent.change(screen.getByLabelText(/table/i), {
      target: { value: "paper_trades" },
    });
    await waitFor(() => {
      expect(mockedAudit).toHaveBeenCalledTimes(2);
    });
    const lastArgs = mockedAudit.mock.calls.at(-1)?.[0];
    expect(lastArgs?.table).toBe("paper_trades");
  });

  test("changing the user_id filter triggers a refetch with user_id param", async () => {
    mockedAudit.mockResolvedValueOnce([entrySample()]);
    render(<AuditTrail />);
    await waitFor(() => {
      expect(mockedAudit).toHaveBeenCalledTimes(1);
    });
    mockedAudit.mockResolvedValueOnce([]);
    fireEvent.change(screen.getByLabelText(/user id/i), {
      target: { value: "42" },
    });
    await waitFor(() => {
      expect(mockedAudit).toHaveBeenCalledTimes(2);
    });
    const lastArgs = mockedAudit.mock.calls.at(-1)?.[0];
    expect(lastArgs?.user_id).toBe(42);
  });

  test("renders empty state when API returns []", async () => {
    mockedAudit.mockResolvedValueOnce([]);
    render(<AuditTrail />);
    await waitFor(() => {
      expect(screen.getByText(/no audit entries/i)).toBeInTheDocument();
    });
  });

  test("renders error alert on API failure", async () => {
    mockedAudit.mockRejectedValueOnce(new Error("HTTP 500 for /admin/audit-trail"));
    render(<AuditTrail />);
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/HTTP 500/);
    });
  });

  test("table renders the system-table user_id placeholder when null", async () => {
    mockedAudit.mockResolvedValueOnce([
      entrySample({ user_id: null, table_name: "auth_violations", summary: "x@e.com reason=blocked" }),
    ]);
    render(<AuditTrail />);
    const rows = await screen.findAllByTestId("audit-row");
    expect(rows[0]).toHaveTextContent("—");
  });
});
