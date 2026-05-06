import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  getSystemState: vi.fn(),
  getSystemLog: vi.fn(),
  pauseSystem: vi.fn(),
  resumeSystem: vi.fn(),
}));

import {
  getSystemState,
  pauseSystem,
  resumeSystem,
} from "@/lib/api";
import { SystemPauseControl } from "@/tabs/Settings/SystemPauseControl";

const mockedGet = vi.mocked(getSystemState);
const mockedPause = vi.mocked(pauseSystem);
const mockedResume = vi.mocked(resumeSystem);

beforeEach(() => {
  // Real timers for the render-and-assert tests; the polling-cadence
  // test below opts into fake timers locally with `shouldAdvanceTime`
  // so testing-library's waitFor still works.
  mockedGet.mockReset();
  mockedPause.mockReset();
  mockedResume.mockReset();
});

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe("SystemPauseControl", () => {
  test("renders Pause button when running", async () => {
    mockedGet.mockResolvedValueOnce({
      paused: false, since: null, by_email: null, reason: null,
    });
    render(<SystemPauseControl />);
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /^pause$/i }),
      ).toBeInTheDocument();
    });
  });

  test("Pause button is disabled with empty reason", async () => {
    mockedGet.mockResolvedValueOnce({
      paused: false, since: null, by_email: null, reason: null,
    });
    render(<SystemPauseControl />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /^pause$/i })).toBeDisabled();
    });
  });

  test("entering reason + clicking Pause calls pauseSystem", async () => {
    mockedGet.mockResolvedValue({
      paused: false, since: null, by_email: null, reason: null,
    });
    mockedPause.mockResolvedValueOnce({
      paused: true, since: "2026-05-06T12:00:00+00:00",
      by_email: "admin@x.com", reason: "travel",
    });
    render(<SystemPauseControl />);
    await waitFor(() => screen.getByRole("button", { name: /^pause$/i }));
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "travel" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^pause$/i }));
    await waitFor(() => {
      expect(mockedPause).toHaveBeenCalledWith("travel");
    });
  });

  test("renders Resume + paused-since text when paused", async () => {
    mockedGet.mockResolvedValueOnce({
      paused: true, since: "2026-05-06T12:34:00+00:00",
      by_email: "admin@x.com", reason: "travel",
    });
    render(<SystemPauseControl />);
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /^resume$/i }),
      ).toBeInTheDocument();
    });
    expect(screen.getByText(/admin@x\.com/)).toBeInTheDocument();
    expect(screen.getByText(/travel/)).toBeInTheDocument();
  });

  test("clicking Resume calls resumeSystem", async () => {
    mockedGet.mockResolvedValue({
      paused: true, since: "2026-05-06T12:00:00+00:00",
      by_email: "admin@x.com", reason: "r",
    });
    mockedResume.mockResolvedValueOnce({
      paused: false, since: null, by_email: null, reason: null,
    });
    render(<SystemPauseControl />);
    await waitFor(() => screen.getByRole("button", { name: /^resume$/i }));
    fireEvent.click(screen.getByRole("button", { name: /^resume$/i }));
    await waitFor(() => {
      expect(mockedResume).toHaveBeenCalledTimes(1);
    });
  });

  test("polls every 5 seconds", async () => {
    mockedGet.mockResolvedValue({
      paused: false, since: null, by_email: null, reason: null,
    });
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<SystemPauseControl />);
    await vi.waitFor(() => expect(mockedGet).toHaveBeenCalledTimes(1));
    await vi.advanceTimersByTimeAsync(5_000);
    await vi.waitFor(() => expect(mockedGet).toHaveBeenCalledTimes(2));
    await vi.advanceTimersByTimeAsync(5_000);
    await vi.waitFor(() => expect(mockedGet).toHaveBeenCalledTimes(3));
  });
});
