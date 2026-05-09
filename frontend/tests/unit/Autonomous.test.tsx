import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { Autonomous } from "@/tabs/Autonomous";

describe("Autonomous tab", () => {
  test("renders all 7 panels", () => {
    render(<Autonomous />);
    // Each panel has a unique <h3> title. Use heading role rather than
    // free text so the assertion doesn't collide with body copy that
    // happens to mention the same words (e.g. "Live positions will
    // appear here..." in the empty-state paragraph).
    for (const title of [
      /Trading Mode/i,
      /Promotion Gates/i,
      /Live Positions/i,
      /Kill Switches/i,
      /Recent Activity/i,
      /Tax Export/i,
      /^Settings$/i,
    ]) {
      expect(screen.getByRole("heading", { name: title })).toBeInTheDocument();
    }
  });

  test("ModeSwitcher renders three mode buttons with locks on the upgrade modes", () => {
    render(<Autonomous />);
    expect(screen.getByText("Manual")).toBeInTheDocument();
    // Locked modes are prefixed with 🔒
    expect(screen.getByText(/🔒.*Telegram approve/i)).toBeInTheDocument();
    expect(screen.getByText(/🔒.*Fully auto/i)).toBeInTheDocument();
  });

  test("KillSwitches lists all 6 switches from spec §11.1", () => {
    render(<Autonomous />);
    for (const switchName of [
      "Daily loss",
      "Consecutive losses",
      "Network outage",
      "Slippage",
      "Liquidation near",
      "Funding rate guard",
    ]) {
      expect(screen.getByText(switchName)).toBeInTheDocument();
    }
  });

  test("GateStatus shows all 6 gate metrics from spec §4.1/4.2", () => {
    render(<Autonomous />);
    // Note: "Closed trades" also appears in TaxExport, so assert it via
    // its row-context (use getAllByText and assert the count instead of
    // getByText which would trip the multi-match guard).
    for (const metric of [
      "Days continuous",
      "Sharpe (annualised)",
      "Max drawdown",
      "Win rate",
      "Profit factor",
    ]) {
      expect(screen.getByText(metric)).toBeInTheDocument();
    }
    // "Closed trades" intentionally appears in both GateStatus and
    // TaxExport (same metric, different aggregation window).
    expect(screen.getAllByText("Closed trades")).toHaveLength(2);
  });

  test("TaxExport shows the financial year", () => {
    render(<Autonomous />);
    expect(screen.getByText("FY2026-27")).toBeInTheDocument();
  });
});
