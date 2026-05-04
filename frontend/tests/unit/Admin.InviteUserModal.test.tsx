import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { InviteUserModal } from "@/tabs/Admin/InviteUserModal";
import type { Invitation } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  api: {
    adminCreateInvitation: vi.fn(),
  },
}));

import { api } from "@/lib/api";
const mockedCreate = vi.mocked(api.adminCreateInvitation);

function invitationSample(overrides: Partial<Invitation> = {}): Invitation {
  return {
    id: 42,
    email: "new@example.com",
    display_name: "New User",
    invited_by: 1,
    invited_at: "2026-05-04T00:00:00Z",
    accepted_at: null,
    cf_access_added: false,
    ...overrides,
  };
}

beforeEach(() => {
  mockedCreate.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("InviteUserModal", () => {
  test("renders form with email, display_name, is_admin checkbox, notes", () => {
    render(<InviteUserModal onClose={() => {}} onCreated={() => {}} />);
    expect(screen.getByLabelText(/email/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/display name/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/notes/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/grant admin role/i)).toBeInTheDocument();
  });

  test("submitting form calls api.adminCreateInvitation with payload", async () => {
    mockedCreate.mockResolvedValueOnce(invitationSample());
    render(<InviteUserModal onClose={() => {}} onCreated={() => {}} />);

    fireEvent.change(screen.getByLabelText(/email/i), {
      target: { value: "new@example.com" },
    });
    fireEvent.change(screen.getByLabelText(/display name/i), {
      target: { value: "New User" },
    });
    fireEvent.click(screen.getByLabelText(/grant admin role/i));
    fireEvent.click(screen.getByRole("button", { name: /^invite$/i }));

    await waitFor(() => {
      expect(mockedCreate).toHaveBeenCalledTimes(1);
    });
    expect(mockedCreate).toHaveBeenCalledWith({
      email: "new@example.com",
      display_name: "New User",
      is_admin: true,
      notes: null,
    });
  });

  test("after successful invite, shows step-2 CF Access UI with copy-able email", async () => {
    mockedCreate.mockResolvedValueOnce(invitationSample({ email: "n@e.com" }));
    render(<InviteUserModal onClose={() => {}} onCreated={() => {}} />);
    fireEvent.change(screen.getByLabelText(/email/i), {
      target: { value: "n@e.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^invite$/i }));

    await waitFor(() => {
      expect(screen.getByTestId("invite-cf-step")).toBeInTheDocument();
    });
    // The step-2 card shows the email + a Copy button + the CF Access steps.
    expect(screen.getByText("n@e.com")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /copy/i })).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: /cloudflare access/i }),
    ).toBeInTheDocument();
  });

  test("step-2 confirm button calls onCreated with the new Invitation", async () => {
    const inv = invitationSample({ id: 99 });
    mockedCreate.mockResolvedValueOnce(inv);
    const onCreated = vi.fn();
    render(<InviteUserModal onClose={() => {}} onCreated={onCreated} />);
    fireEvent.change(screen.getByLabelText(/email/i), {
      target: { value: "new@example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^invite$/i }));

    await waitFor(() => {
      expect(screen.getByTestId("invite-cf-step")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /added the email/i }));
    expect(onCreated).toHaveBeenCalledWith(inv);
  });

  test("API error renders alert and stays on form", async () => {
    mockedCreate.mockRejectedValueOnce(new Error("HTTP 409 for /admin/invitations"));
    render(<InviteUserModal onClose={() => {}} onCreated={() => {}} />);
    fireEvent.change(screen.getByLabelText(/email/i), {
      target: { value: "x@e.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^invite$/i }));
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/HTTP 409/);
    });
    // Still on step 1: cf step not visible.
    expect(screen.queryByTestId("invite-cf-step")).toBeNull();
  });

  test("Cancel calls onClose", () => {
    const onClose = vi.fn();
    render(<InviteUserModal onClose={onClose} onCreated={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
