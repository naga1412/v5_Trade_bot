import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import type { ScannerFilterCounts } from "@/lib/api";
import { FilterPills } from "@/tabs/Tab3Scanner/FilterPills";

const counts: ScannerFilterCounts = {
  all: 200,
  confirmed: 12,
  probable: 34,
  weak: 140,
  diverging: 14,
};

describe("FilterPills", () => {
  test("renders all 7 pills with correct labels", () => {
    render(<FilterPills active="all" counts={counts} onChange={() => {}} />);
    expect(screen.getByRole("button", { name: /All/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Confirmed/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Probable/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Weak/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Diverging/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Hybrid/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Analyzing/ })).toBeInTheDocument();
  });

  test("shows counts from filter_counts when provided", () => {
    render(<FilterPills active="all" counts={counts} onChange={() => {}} />);
    expect(screen.getByRole("button", { name: /All 200/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Confirmed 12/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Probable 34/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Weak 140/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Diverging 14/ })).toBeInTheDocument();
  });

  test("omits numeric counts when counts prop is null", () => {
    render(<FilterPills active="all" counts={null} onChange={() => {}} />);
    const allBtn = screen.getByRole("button", { name: /All/ });
    expect(allBtn.textContent).not.toMatch(/\d/);
  });

  test("clicking a pill fires onChange with the right id", () => {
    const onChange = vi.fn();
    render(<FilterPills active="all" counts={counts} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: /Confirmed/ }));
    expect(onChange).toHaveBeenCalledWith("confirmed");
    fireEvent.click(screen.getByRole("button", { name: /Diverging/ }));
    expect(onChange).toHaveBeenCalledWith("diverging");
    fireEvent.click(screen.getByRole("button", { name: /Hybrid/ }));
    expect(onChange).toHaveBeenCalledWith("hybrid");
  });

  test("active pill carries data-active=true", () => {
    render(<FilterPills active="probable" counts={counts} onChange={() => {}} />);
    const probable = screen.getByRole("button", { name: /Probable/ });
    expect(probable.getAttribute("data-active")).toBe("true");
    const all = screen.getByRole("button", { name: /All/ });
    expect(all.getAttribute("data-active")).toBe("false");
  });
});
