import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { UserRowMenu } from "@/tabs/Admin/UserRowMenu";
import type { AdminUser } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  api: {
    adminPatchUser: vi.fn(),
    adminDeleteUser: vi.fn(),
    adminImpersonate: vi.fn(),
  },
}));

import { api } from "@/lib/api";
const mockedPatch = vi.mocked(api.adminPatchUser);
const mockedDelete = vi.mocked(api.adminDeleteUser);
const mockedImpersonate = vi.mocked(api.adminImpersonate);

function userSample(overrides: Partial<AdminUser> = {}): AdminUser {
  return {
    id: 7,
    email: "target@example.com",
    display_name: "Target",
    is_admin: false,
    is_active: true,
    trading_mode: "manual",
    last_login: null,
    created_at: "2026-04-01T00:00:00Z",
    invited_by: 1,
    ...overrides,
  };
}

let reloadSpy: ReturnType<typeof vi.fn>;

beforeEach(() => {
  mockedPatch.mockReset();
  mockedDelete.mockReset();
  mockedImpersonate.mockReset();
  reloadSpy = vi.fn();
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { ...window.location, hash: "", reload: reloadSpy },
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("UserRowMenu", () => {
  test("trigger toggles menu open/closed", () => {
    render(<UserRowMenu user={userSample()} onChanged={() => {}} />);
    expect(screen.queryByRole("menu")).toBeNull();
    fireEvent.click(screen.getByTestId("row-menu-trigger-7"));
    expect(screen.getByRole("menu")).toBeInTheDocument();
  });

  test("menu has 4 actions: View as user / Toggle active / Toggle admin / Delete", () => {
    render(<UserRowMenu user={userSample()} onChanged={() => {}} />);
    fireEvent.click(screen.getByTestId("row-menu-trigger-7"));
    expect(screen.getByRole("menuitem", { name: /view as user/i })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /deactivate/i })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /promote to admin/i })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /delete/i })).toBeInTheDocument();
  });

  test("View as user calls api.adminImpersonate(id) and triggers full reload (J4)", async () => {
    mockedImpersonate.mockResolvedValueOnce({
      admin_user_id: 1,
      target_user_id: 7,
      started_at: "2026-05-04T00:00:00Z",
    });
    render(<UserRowMenu user={userSample()} onChanged={() => {}} />);
    fireEvent.click(screen.getByTestId("row-menu-trigger-7"));
    fireEvent.click(screen.getByRole("menuitem", { name: /view as user/i }));
    await waitFor(() => {
      expect(mockedImpersonate).toHaveBeenCalledWith(7);
    });
    expect(reloadSpy).toHaveBeenCalledTimes(1);
  });

  test("Deactivate calls adminPatchUser with is_active toggled to false", async () => {
    mockedPatch.mockResolvedValueOnce(userSample({ is_active: false }));
    const onChanged = vi.fn();
    render(<UserRowMenu user={userSample({ is_active: true })} onChanged={onChanged} />);
    fireEvent.click(screen.getByTestId("row-menu-trigger-7"));
    fireEvent.click(screen.getByRole("menuitem", { name: /deactivate/i }));
    await waitFor(() => {
      expect(mockedPatch).toHaveBeenCalledWith(7, { is_active: false });
    });
    expect(onChanged).toHaveBeenCalled();
  });

  test("Reactivate calls adminPatchUser with is_active=true on inactive user", async () => {
    mockedPatch.mockResolvedValueOnce(userSample({ is_active: true }));
    render(
      <UserRowMenu user={userSample({ is_active: false })} onChanged={() => {}} />,
    );
    fireEvent.click(screen.getByTestId("row-menu-trigger-7"));
    fireEvent.click(screen.getByRole("menuitem", { name: /reactivate/i }));
    await waitFor(() => {
      expect(mockedPatch).toHaveBeenCalledWith(7, { is_active: true });
    });
  });

  test("Promote to admin calls adminPatchUser with is_admin=true", async () => {
    mockedPatch.mockResolvedValueOnce(userSample({ is_admin: true }));
    render(<UserRowMenu user={userSample({ is_admin: false })} onChanged={() => {}} />);
    fireEvent.click(screen.getByTestId("row-menu-trigger-7"));
    fireEvent.click(screen.getByRole("menuitem", { name: /promote to admin/i }));
    await waitFor(() => {
      expect(mockedPatch).toHaveBeenCalledWith(7, { is_admin: true });
    });
  });

  test("Delete calls adminDeleteUser(id) and reloads", async () => {
    mockedDelete.mockResolvedValueOnce(undefined);
    const onChanged = vi.fn();
    render(<UserRowMenu user={userSample()} onChanged={onChanged} />);
    fireEvent.click(screen.getByTestId("row-menu-trigger-7"));
    fireEvent.click(screen.getByRole("menuitem", { name: /delete/i }));
    await waitFor(() => {
      expect(mockedDelete).toHaveBeenCalledWith(7);
    });
    expect(onChanged).toHaveBeenCalled();
  });

  test("trigger button meets 44px touch target", () => {
    render(<UserRowMenu user={userSample()} onChanged={() => {}} />);
    const trigger = screen.getByTestId("row-menu-trigger-7");
    expect(trigger.className).toMatch(/min-h-\[44px\]/);
  });
});
