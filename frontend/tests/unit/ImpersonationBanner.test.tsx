import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import { ImpersonationBanner } from "@/components/layout/ImpersonationBanner";

describe("ImpersonationBanner", () => {
  test("renders the impersonated email", () => {
    render(
      <ImpersonationBanner
        viewingEmail="target@example.com"
        onExit={() => {}}
      />,
    );
    expect(screen.getByTestId("impersonation-banner")).toBeInTheDocument();
    expect(screen.getByText(/target@example.com/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /exit impersonation/i }),
    ).toBeInTheDocument();
  });

  test("clicking Exit calls onExit handler", () => {
    const onExit = vi.fn();
    render(
      <ImpersonationBanner viewingEmail="t@e.com" onExit={onExit} />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: /exit impersonation/i }),
    );
    expect(onExit).toHaveBeenCalledTimes(1);
  });

  test("Exit button meets 44px touch target on mobile (min-h-[44px])", () => {
    render(
      <ImpersonationBanner viewingEmail="t@e.com" onExit={() => {}} />,
    );
    const btn = screen.getByRole("button", { name: /exit impersonation/i });
    expect(btn.className).toMatch(/min-h-\[44px\]/);
  });

  test("uses red background for high-visibility warning", () => {
    render(
      <ImpersonationBanner viewingEmail="t@e.com" onExit={() => {}} />,
    );
    const banner = screen.getByTestId("impersonation-banner");
    expect(banner.className).toMatch(/bg-red/);
  });
});
