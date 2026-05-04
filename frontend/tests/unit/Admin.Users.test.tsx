import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { Users } from "@/tabs/Admin/Users";
import type { AdminUser } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  api: {
    adminListUsers: vi.fn(),
    adminCreateInvitation: vi.fn(),
    adminPatchUser: vi.fn(),
    adminDeleteUser: vi.fn(),
    adminImpersonate: vi.fn(),
  },
}));

import { api } from "@/lib/api";
const mockedList = vi.mocked(api.adminListUsers);

function userSample(overrides: Partial<AdminUser> = {}): AdminUser {
  return {
    id: 1,
    email: "user1@example.com",
    display_name: "User One",
    is_admin: false,
    is_active: true,
    trading_mode: "manual",
    last_login: "2026-05-01T12:00:00Z",
    created_at: "2026-04-01T00:00:00Z",
    invited_by: null,
    ...overrides,
  };
}

beforeEach(() => {
  mockedList.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("Admin/Users page", () => {
  test("renders rows with email + status pill + last_login + mode", async () => {
    mockedList.mockResolvedValueOnce([
      userSample({ id: 1, email: "alice@x.com" }),
      userSample({ id: 2, email: "bob@x.com", is_active: false }),
      userSample({ id: 3, email: "admin@x.com", is_admin: true }),
    ]);
    render(<Users />);
    await waitFor(() => {
      expect(screen.getByText("alice@x.com")).toBeInTheDocument();
    });
    expect(screen.getByText("bob@x.com")).toBeInTheDocument();
    expect(screen.getByText("admin@x.com")).toBeInTheDocument();
    // Each row gets a status pill (3 users -> 3 pills).
    expect(screen.getAllByTestId("status-pill")).toHaveLength(3);
  });

  test("admin badge appears next to admin row only", async () => {
    mockedList.mockResolvedValueOnce([
      userSample({ id: 1, email: "user@x.com", is_admin: false }),
      userSample({ id: 2, email: "admin@x.com", is_admin: true }),
    ]);
    render(<Users />);
    await waitFor(() => {
      expect(screen.getByText("admin@x.com")).toBeInTheDocument();
    });
    const badges = screen.getAllByTestId("admin-badge");
    expect(badges).toHaveLength(1);
  });

  test("status pill text reflects is_active", async () => {
    mockedList.mockResolvedValueOnce([
      userSample({ id: 1, is_active: true }),
      userSample({ id: 2, email: "u2@x.com", is_active: false }),
    ]);
    render(<Users />);
    await waitFor(() => {
      expect(screen.getByText("u2@x.com")).toBeInTheDocument();
    });
    const pills = screen.getAllByTestId("status-pill");
    expect(pills[0]).toHaveTextContent(/active/i);
    expect(pills[1]).toHaveTextContent(/revoked/i);
  });

  test("renders empty state when no users", async () => {
    mockedList.mockResolvedValueOnce([]);
    render(<Users />);
    await waitFor(() => {
      expect(screen.getByText(/no users/i)).toBeInTheDocument();
    });
  });

  test("renders error when API fails", async () => {
    mockedList.mockRejectedValueOnce(new Error("HTTP 500 for /admin/users"));
    render(<Users />);
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/HTTP 500/);
    });
  });

  test("clicking + Invite opens the InviteUserModal", async () => {
    mockedList.mockResolvedValueOnce([userSample()]);
    render(<Users />);
    await waitFor(() => {
      expect(screen.getByText("user1@example.com")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /invite/i }));
    expect(screen.getByTestId("invite-modal")).toBeInTheDocument();
  });
});
