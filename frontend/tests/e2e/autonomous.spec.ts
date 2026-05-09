// SP-8 Phase J: Playwright E2E for the Autonomous tab.
//
// Smoke-grade end-to-end coverage of the eight interactive panels.
// Each assertion anchors on the rendered DOM (role + accessible name)
// rather than free text, so harmless copy edits don't break the test.
//
// Backend-state assumptions — the dev backend boots with no live
// trading and no tax events, so the panels should render their
// empty-state shapes (zero counts, "not set up" badges, etc.).
// We do NOT exercise mode upgrades or kill-switch toggles end-to-end
// here because those mutate the dev DB and require TOTP — those
// flows are covered by the unit tests on the individual panels.

import { expect, test } from "@playwright/test";

test.describe("Autonomous tab", () => {
  test("renders all 8 panels with their expected interactive controls", async ({
    page,
  }) => {
    await page.goto("/");
    await page.getByRole("tab", { name: /autonomous/i }).click();
    await expect(page).toHaveURL(/#\/autonomous/);

    // Each Panel's title is an <h3>. Asserting all eight in one pass
    // proves the layout composition didn't regress.
    for (const title of [
      /Trading Mode/i,
      /Promotion Gates/i,
      /Live Positions/i,
      /Kill Switches/i,
      /Recent Activity/i,
      /Tax Export \(Schedule VDA\)/i,
      /^Settings$/i,
      /TOTP \(Two-Factor\)/i,
    ]) {
      await expect(
        page.getByRole("heading", { name: title }),
      ).toBeVisible();
    }
  });

  test("ModeSwitcher exposes three role=group buttons + active=Manual", async ({
    page,
  }) => {
    await page.goto("/#/autonomous");
    const group = page.getByRole("group", { name: /trading mode/i });
    await expect(group).toBeVisible();
    for (const label of [/^manual$/i, /telegram approve/i, /fully auto/i]) {
      await expect(
        group.getByRole("button", { name: label }),
      ).toBeVisible();
    }
    // Manual is the default seed for dev users.
    await expect(
      group.getByRole("button", { name: /^manual$/i }),
    ).toHaveAttribute("aria-pressed", "true");
  });

  test("KillSwitches loads all 6 switches with Disable buttons", async ({
    page,
  }) => {
    await page.goto("/#/autonomous");
    for (const label of [
      "Daily loss",
      "Consecutive losses",
      "Network outage",
      "Slippage",
      "Liquidation near",
      "Funding rate guard",
    ]) {
      await expect(page.getByText(label, { exact: true })).toBeVisible();
    }
    // 6 disable buttons expected (all switches default-armed).
    await expect(
      page.getByRole("button", { name: /^disable / }),
    ).toHaveCount(6);
  });

  test("TaxExport offers an FY selector and a download anchor", async ({
    page,
  }) => {
    await page.goto("/#/autonomous");
    await expect(page.getByLabel(/^fy$/i)).toBeVisible();
    const link = page.getByRole("link", {
      name: /download schedule-vda csv/i,
    });
    await expect(link).toBeVisible();
    await expect(link).toHaveAttribute(
      "href",
      /\/api\/v1\/me\/tax\/export\?fy_year=FY\d{4}-\d{2}/,
    );
  });

  test("AutonomousSettings renders editable numeric inputs", async ({
    page,
  }) => {
    await page.goto("/#/autonomous");
    for (const label of [
      /min size USDT/i,
      /max size USDT/i,
      /max leverage/i,
    ]) {
      await expect(page.getByLabel(label)).toBeVisible();
    }
    // Save button starts disabled (no edits yet).
    await expect(
      page.getByRole("button", { name: /^save$/i }),
    ).toBeDisabled();
  });

  test("TotpSetupWizard exposes the Set up TOTP entry point", async ({
    page,
  }) => {
    await page.goto("/#/autonomous");
    // Either "Set up TOTP" (not configured) or "Re-run setup"
    // (configured) — the dev seed leaves it un-configured, so the
    // primary CTA is "Set up TOTP".
    await expect(
      page.getByRole("button", { name: /set up totp/i }),
    ).toBeVisible();
  });
});
