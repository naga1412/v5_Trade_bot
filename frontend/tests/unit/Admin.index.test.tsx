import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { Admin } from "@/tabs/Admin";

// SP-6 Phase E4/E5: verify the Admin tab now exposes 6 sub-tabs
// (Users / Audit Trail / ML Checkpoints / Patterns / Adapters / Traps) and
// that switching between them mounts the right component.

vi.mock("@/lib/api", () => ({
  api: {
    adminListUsers: vi.fn().mockResolvedValue([]),
    adminAuditTrail: vi.fn().mockResolvedValue([]),
    adminListMlCheckpoints: vi.fn().mockResolvedValue([]),
    adminListPatterns: vi.fn().mockResolvedValue([]),
    adminListAdapters: vi.fn().mockResolvedValue([]),
    adminListTraps: vi.fn().mockResolvedValue([]),
  },
}));

import { api } from "@/lib/api";
const mockedListPatterns = vi.mocked(api.adminListPatterns);
const mockedListAdapters = vi.mocked(api.adminListAdapters);
const mockedListTraps = vi.mocked(api.adminListTraps);

beforeEach(() => {
  mockedListPatterns.mockClear();
  mockedListAdapters.mockClear();
  mockedListTraps.mockClear();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("Admin index tab routing", () => {
  test("renders all 6 sub-tabs", () => {
    render(<Admin />);
    for (const label of [
      "Users",
      "Audit Trail",
      "ML Checkpoints",
      "Patterns",
      "Adapters",
      "Traps",
    ]) {
      expect(
        screen.getByRole("tab", { name: new RegExp(label, "i") }),
      ).toBeInTheDocument();
    }
  });

  test("clicking Patterns tab mounts PatternsAdmin (calls adminListPatterns)", async () => {
    render(<Admin />);
    fireEvent.click(screen.getByRole("tab", { name: /patterns/i }));
    await waitFor(() => {
      expect(mockedListPatterns).toHaveBeenCalled();
    });
  });

  test("clicking Adapters tab mounts AdaptersAdmin (calls adminListAdapters)", async () => {
    render(<Admin />);
    fireEvent.click(screen.getByRole("tab", { name: /adapters/i }));
    await waitFor(() => {
      expect(mockedListAdapters).toHaveBeenCalled();
    });
  });

  test("clicking Traps tab mounts TrapsAdmin (calls adminListTraps)", async () => {
    render(<Admin />);
    fireEvent.click(screen.getByRole("tab", { name: /traps/i }));
    await waitFor(() => {
      expect(mockedListTraps).toHaveBeenCalled();
    });
  });
});
