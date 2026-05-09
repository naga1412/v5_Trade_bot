import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { Autonomous } from "@/tabs/Autonomous";

describe("Autonomous tab", () => {
  test("renders all 7 panels", () => {
    render(<Autonomous />);
    // Each panel has a unique title.
    for (const title of [
      /Trading Mode/i,
      /Promotion Gates/i,
      /Live Positions/i,
      /Kill Switches/i,
      /Recent Activity/i,
      /Tax Export/i,
      /^Settings$/i,
    ]) {
      expect(screen.getByText(title)).toBeInTheDocument();
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
    for (const metric of [
      "Days continuous",
      "Closed trades",
      "Sharpe (annualised)",
      "Max drawdown",
      "Win rate",
      "Profit factor",
    ]) {
      expect(screen.getByText(metric)).toBeInTheDocument();
    }
  });

  test("TaxExport shows the financial year", () => {
    render(<Autonomous />);
    expect(screen.getByText("FY2026-27")).toBeInTheDocument();
  });
});
