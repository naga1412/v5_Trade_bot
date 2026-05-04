import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { MlCheckpoints } from "@/tabs/Admin/MlCheckpoints";
import type { MlCheckpoint } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  api: {
    adminListMlCheckpoints: vi.fn(),
    adminPatchMlCheckpoint: vi.fn(),
    adminDeleteMlCheckpoint: vi.fn(),
  },
}));

import { api } from "@/lib/api";
const mockedList = vi.mocked(api.adminListMlCheckpoints);
const mockedPatch = vi.mocked(api.adminPatchMlCheckpoint);
const mockedDelete = vi.mocked(api.adminDeleteMlCheckpoint);

function ckpt(overrides: Partial<MlCheckpoint> = {}): MlCheckpoint {
  return {
    id: 1,
    model_name: "conv_lstm_predictor",
    version: "0.1.0",
    checkpoint_uri: "b2://x/v1.pt",
    sha256: "a".repeat(64),
    trained_at: "2026-05-15T12:00:00Z",
    train_data_window: "2017-08 to 2023-12",
    eval_results: { bull_breakout: 0.013 },
    is_active: false,
    activated_at: null,
    deactivated_at: null,
    notes: null,
    ...overrides,
  };
}

beforeEach(() => {
  mockedList.mockReset();
  mockedPatch.mockReset();
  mockedDelete.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("Admin/MlCheckpoints page", () => {
  test("renders rows with version + model_name + status pill", async () => {
    mockedList.mockResolvedValueOnce([
      ckpt({ id: 1, version: "0.1.0", is_active: true }),
      ckpt({ id: 2, version: "0.2.0" }),
    ]);
    render(<MlCheckpoints />);
    await waitFor(() => {
      expect(screen.getByText("0.1.0")).toBeInTheDocument();
    });
    expect(screen.getByText("0.2.0")).toBeInTheDocument();
    expect(screen.getAllByTestId("ckpt-status-pill")).toHaveLength(2);
  });

  test("active pill labels active row", async () => {
    mockedList.mockResolvedValueOnce([
      ckpt({ id: 1, version: "0.1.0", is_active: true }),
      ckpt({ id: 2, version: "0.2.0", is_active: false }),
    ]);
    render(<MlCheckpoints />);
    await waitFor(() => {
      expect(screen.getByText("0.1.0")).toBeInTheDocument();
    });
    const pills = screen.getAllByTestId("ckpt-status-pill");
    expect(pills[0]).toHaveTextContent(/active/i);
    expect(pills[1]).toHaveTextContent(/inactive/i);
  });

  test("clicking Activate calls adminPatchMlCheckpoint and reloads", async () => {
    mockedList.mockResolvedValueOnce([
      ckpt({ id: 7, version: "0.5.0", is_active: false }),
    ]);
    mockedPatch.mockResolvedValueOnce(
      ckpt({ id: 7, version: "0.5.0", is_active: true }),
    );
    mockedList.mockResolvedValueOnce([
      ckpt({ id: 7, version: "0.5.0", is_active: true }),
    ]);
    render(<MlCheckpoints />);
    await waitFor(() => {
      expect(screen.getByText("0.5.0")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /activate/i }));
    await waitFor(() => {
      expect(mockedPatch).toHaveBeenCalledWith(7, { is_active: true });
    });
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledTimes(2);
    });
  });

  test("clicking Deactivate calls adminPatchMlCheckpoint with is_active=false", async () => {
    mockedList.mockResolvedValueOnce([
      ckpt({ id: 9, version: "0.6.0", is_active: true }),
    ]);
    mockedPatch.mockResolvedValueOnce(
      ckpt({ id: 9, version: "0.6.0", is_active: false }),
    );
    mockedList.mockResolvedValueOnce([
      ckpt({ id: 9, version: "0.6.0", is_active: false }),
    ]);
    render(<MlCheckpoints />);
    await waitFor(() => {
      expect(screen.getByText("0.6.0")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /deactivate/i }));
    await waitFor(() => {
      expect(mockedPatch).toHaveBeenCalledWith(9, { is_active: false });
    });
  });

  test("renders eval_results summary as JSON-ish pairs", async () => {
    mockedList.mockResolvedValueOnce([
      ckpt({
        id: 1,
        version: "0.1.0",
        eval_results: { bull_breakout: 0.013, bear_crash: 0.025 },
      }),
    ]);
    render(<MlCheckpoints />);
    await waitFor(() => {
      expect(screen.getByText("0.1.0")).toBeInTheDocument();
    });
    expect(screen.getByText(/bull_breakout/)).toBeInTheDocument();
    expect(screen.getByText(/bear_crash/)).toBeInTheDocument();
  });

  test("renders empty state when no checkpoints", async () => {
    mockedList.mockResolvedValueOnce([]);
    render(<MlCheckpoints />);
    await waitFor(() => {
      expect(screen.getByText(/no checkpoints/i)).toBeInTheDocument();
    });
  });

  test("renders error when API fails", async () => {
    mockedList.mockRejectedValueOnce(
      new Error("HTTP 500 for /admin/ml-checkpoints"),
    );
    render(<MlCheckpoints />);
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/HTTP 500/);
    });
  });
});
