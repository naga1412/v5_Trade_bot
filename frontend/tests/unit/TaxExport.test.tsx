/**
 * SP-8 Phase J — TaxExport interactive tests.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { TaxExport } from "@/tabs/Autonomous/panels/TaxExport";
import * as apiMod from "@/lib/api";

beforeEach(() => {
  vi.restoreAllMocks();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("TaxExport", () => {
  test("loads summary on mount + renders totals", async () => {
    const spy = vi
      .spyOn(apiMod.api, "meTaxSummary")
      .mockResolvedValue({
        fy_year: "FY2026-27",
        total_trades: 4,
        total_realized_pnl_inr: 12345.67,
        total_tds_inr: 247.5,
        total_fees_inr: 50,
      });
    render(<TaxExport />);
    await waitFor(() => {
      expect(spy).toHaveBeenCalled();
    });
    expect(await screen.findByText("4")).toBeInTheDocument();
    // INR formatting includes the rupee sign + comma separators.
    expect(screen.getByText(/₹12,345/)).toBeInTheDocument();
  });

  test("download anchor points at /me/tax/export with the FY", async () => {
    vi.spyOn(apiMod.api, "meTaxSummary").mockResolvedValue({
      fy_year: "FY2026-27",
      total_trades: 0,
      total_realized_pnl_inr: 0,
      total_tds_inr: 0,
      total_fees_inr: 0,
    });
    render(<TaxExport />);
    const link = await screen.findByRole("link", {
      name: /download schedule-vda csv/i,
    });
    expect(link.getAttribute("href")).toMatch(
      /\/me\/tax\/export\?fy_year=FY\d{4}-\d{2}/,
    );
  });

  test("changing FY refetches the summary", async () => {
    const spy = vi
      .spyOn(apiMod.api, "meTaxSummary")
      .mockResolvedValue({
        fy_year: "FY2025-26",
        total_trades: 7,
        total_realized_pnl_inr: 100,
        total_tds_inr: 1,
        total_fees_inr: 0,
      });
    render(<TaxExport />);
    await waitFor(() => {
      expect(spy).toHaveBeenCalledTimes(1);
    });
    const select = screen.getByLabelText(/^fy$/i) as HTMLSelectElement;
    const otherOption = Array.from(select.options).find(
      (o) => o.value !== select.value,
    );
    if (!otherOption) throw new Error("expected at least one alt FY option");
    fireEvent.change(select, { target: { value: otherOption.value } });
    await waitFor(() => {
      expect(spy).toHaveBeenCalledTimes(2);
    });
  });

  test("API error surfaces as alert", async () => {
    vi.spyOn(apiMod.api, "meTaxSummary").mockRejectedValue(
      new Error("server down"),
    );
    render(<TaxExport />);
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/server down/i);
    });
  });
});
