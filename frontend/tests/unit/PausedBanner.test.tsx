import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  getSystemState: vi.fn(),
}));

import { getSystemState } from "@/lib/api";
import { PausedBanner } from "@/components/layout/PausedBanner";

const mockedGet = vi.mocked(getSystemState);

beforeEach(() => {
  mockedGet.mockReset();
});
afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe("PausedBanner", () => {
  test("renders nothing when not paused", async () => {
    mockedGet.mockResolvedValue({
      paused: false, since: null, by_email: null, reason: null,
    });
    const { container } = render(<PausedBanner />);
    await waitFor(() => expect(mockedGet).toHaveBeenCalled());
    expect(container.querySelector('[data-testid="paused-banner"]')).toBeNull();
  });

  test("renders banner with message when paused", async () => {
    mockedGet.mockResolvedValue({
      paused: true, since: "2026-05-06T12:00:00+00:00",
      by_email: "admin@x.com", reason: "travel",
    });
    render(<PausedBanner />);
    await waitFor(() => {
      expect(screen.getByTestId("paused-banner")).toBeInTheDocument();
    });
    expect(screen.getByText(/Read-only mode/i)).toBeInTheDocument();
  });

  test("re-polls every 5 seconds", async () => {
    mockedGet.mockResolvedValue({
      paused: false, since: null, by_email: null, reason: null,
    });
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<PausedBanner />);
    await vi.waitFor(() => expect(mockedGet).toHaveBeenCalledTimes(1));
    await vi.advanceTimersByTimeAsync(5_000);
    await vi.waitFor(() => expect(mockedGet).toHaveBeenCalledTimes(2));
  });

  test("uses yellow background for warning visibility", async () => {
    mockedGet.mockResolvedValue({
      paused: true, since: null, by_email: null, reason: null,
    });
    render(<PausedBanner />);
    await waitFor(() => screen.getByTestId("paused-banner"));
    expect(screen.getByTestId("paused-banner").className).toMatch(/bg-yellow/);
  });
});
