// SP-0.5 Phase P2: Playwright E2E for the Bot Status tab.
//
// Resilient to empty / seeded backend states — these smoke tests assert the
// shell renders, not specific values. Mobile and desktop both run via the
// projects defined in playwright.config.ts (chromium-desktop + mobile-iphone).

import { expect, test } from "@playwright/test";

// Panel titles are taken verbatim from the components in
// frontend/src/tabs/BotStatus/*.tsx so a renaming there will surface here.
const SECTION_TITLES = [
  "Overview",
  "Promotion Gate",
  "Open Positions",
  "Per-Asset Stats",
  "Long vs Short",
  "Equity Curve",
  "Recent Trades",
] as const;

test.describe("Bot Status tab", () => {
  test("nav: clicking Bot Status switches view and updates URL hash", async ({
    page,
  }) => {
    await page.goto("/");
    // The tab nav exposes role="tab" buttons; "Bot Status" is the second tab.
    await page.getByRole("tab", { name: "Bot Status" }).click();
    await expect(page).toHaveURL(/#\/bot-status/);
    // Promotion Gate is one of the headline panels and a stable signal that
    // the BotStatus tree mounted (vs LivePrediction's chart canvas).
    await expect(page.locator("text=Promotion Gate").first()).toBeVisible();
  });

  test("renders all 7 sections via deep-link", async ({ page }) => {
    await page.goto("/#/bot-status");
    // Sections fetch async; allow REST + WS handshake to settle.
    await page.waitForLoadState("networkidle");
    for (const title of SECTION_TITLES) {
      // EquityCurve renders "Equity Curve (90d)" once data arrives, hence
      // a substring match (.first() in case the same string also appears in
      // a header subtitle).
      await expect(
        page.locator(`text=/${title}/i`).first(),
      ).toBeVisible({ timeout: 10_000 });
    }
  });

  test("mobile: sections stack vertically without horizontal scroll", async ({
    page,
    isMobile,
  }) => {
    test.skip(!isMobile, "mobile-only layout assertion");

    await page.goto("/#/bot-status");
    await page.waitForLoadState("networkidle");

    // No horizontal overflow on mobile.
    const scrollWidth = await page.evaluate(
      () => document.documentElement.scrollWidth,
    );
    const clientWidth = await page.evaluate(
      () => document.documentElement.clientWidth,
    );
    // Allow a 1px rounding fudge factor; in practice they're equal.
    expect(scrollWidth).toBeLessThanOrEqual(clientWidth + 1);

    // Each section heading should still be visible after a viewport reset.
    for (const title of SECTION_TITLES) {
      await expect(
        page.locator(`text=/${title}/i`).first(),
      ).toBeVisible({ timeout: 10_000 });
    }
  });

  test("recent trades signal deep-link routes to Live Prediction", async ({
    page,
  }) => {
    await page.goto("/#/bot-status");
    await page.waitForLoadState("networkidle");

    // Recent Trades renders an <a href="#/live-prediction?signal=..."> on each
    // row's time cell. On a fresh DB the table may be empty — skip gracefully.
    const link = page.locator('a[href*="#/live-prediction?signal="]').first();
    const linkCount = await link.count();
    test.skip(linkCount === 0, "no recent trades to test deeplink");

    await link.click();
    await expect(page).toHaveURL(/#\/live-prediction\?signal=/);
  });
});
