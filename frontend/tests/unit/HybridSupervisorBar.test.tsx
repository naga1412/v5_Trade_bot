import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { HybridSupervisorBar } from "@/tabs/Tab3Scanner/HybridSupervisorBar";

describe("HybridSupervisorBar", () => {
  test("renders X/N done label and bar at correct width", () => {
    const { container } = render(
      <HybridSupervisorBar progress={{ done: 3, total: 8 }} />,
    );
    expect(screen.getByText(/3\/8 done/i)).toBeInTheDocument();
    const fills = container.querySelectorAll("div[style]");
    const fill = Array.from(fills).find(
      (el) => (el as HTMLElement).style.width === "37.5%",
    );
    expect(fill).toBeDefined();
  });

  test("renders 0/8 done with 0 width when progress is null", () => {
    const { container } = render(<HybridSupervisorBar progress={null} />);
    expect(screen.getByText(/0\/8 done/i)).toBeInTheDocument();
    const fills = container.querySelectorAll("div[style]");
    const fill = Array.from(fills).find(
      (el) => (el as HTMLElement).style.width === "0%",
    );
    expect(fill).toBeDefined();
  });

  test("clamps total at 1 to avoid divide-by-zero", () => {
    const { container } = render(
      <HybridSupervisorBar progress={{ done: 0, total: 0 }} />,
    );
    expect(screen.getByText(/0\/0 done/i)).toBeInTheDocument();
    const fills = container.querySelectorAll("div[style]");
    expect(fills.length).toBeGreaterThan(0);
  });

  test("renders Hybrid Supervisor section label", () => {
    render(<HybridSupervisorBar progress={{ done: 1, total: 8 }} />);
    expect(screen.getByText(/Hybrid Supervisor/i)).toBeInTheDocument();
  });
});
