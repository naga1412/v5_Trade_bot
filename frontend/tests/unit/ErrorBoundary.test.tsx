/**
 * SP-9 — ErrorBoundary tests.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

import { ErrorBoundary } from "@/components/ErrorBoundary";

function Boom({ message = "kaboom" }: { message?: string }): JSX.Element {
  throw new Error(message);
}

describe("ErrorBoundary", () => {
  test("renders children when no error", () => {
    render(
      <ErrorBoundary label="test">
        <div data-testid="child">hello</div>
      </ErrorBoundary>,
    );
    expect(screen.getByTestId("child")).toBeInTheDocument();
  });

  test("catches a render error + shows the label + message", () => {
    // Suppress the noisy React error output for this test.
    const consoleError = vi
      .spyOn(console, "error")
      .mockImplementation(() => {});
    render(
      <ErrorBoundary label="bot status">
        <Boom message="overview blew up" />
      </ErrorBoundary>,
    );
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByText(/bot status crashed/i)).toBeInTheDocument();
    expect(screen.getByText(/overview blew up/i)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /retry/i }),
    ).toBeInTheDocument();
    consoleError.mockRestore();
  });

  test("Retry button resets state so a fixed child can render again", () => {
    const consoleError = vi
      .spyOn(console, "error")
      .mockImplementation(() => {});
    let shouldThrow = true;
    function Conditional() {
      if (shouldThrow) throw new Error("first time");
      return <div data-testid="ok">recovered</div>;
    }
    render(
      <ErrorBoundary label="test">
        <Conditional />
      </ErrorBoundary>,
    );
    expect(screen.getByText(/first time/i)).toBeInTheDocument();
    shouldThrow = false;
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(screen.getByTestId("ok")).toBeInTheDocument();
    consoleError.mockRestore();
  });
});
