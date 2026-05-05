// SP-6 Phase F4: Playwright E2E for Tab 3 Scanner Radar.
//
// Mirrors bot-status.spec.ts in style — resilient to empty backend states.
// Asserts:
//   1. Clicking the "Scanner" tab nav switches view + updates URL hash.
//   2. Deep-linking to #/scanner renders the toolbar shell + columns.
//   3. Clicking a SignalCard navigates to Tab 1 with ?symbol=&tf= query.
//      Falls back to skipping when the backend has no cards yet.
//   4. Mobile (iPhone 13): bullish + bearish columns stack vertically with
//      no horizontal document overflow.
//
// Both projects (chromium-desktop + mobile-iphone) run these — the mobile
// case is gated on `isMobile`.

import { expect, test } from "@playwright/test";

test.describe("Tab 3 Scanner", () => {
  test("nav: clicking Scanner switches view and updates URL hash", async ({
    page,
  }) => {
    await page.goto("/");
    await page.getByRole("tab", { name: "Scanner" }).click();
    await expect(page).toHaveURL(/#\/scanner/);
    // Toolbar is the stable signal that Tab3Scanner mounted.
    await expect(
      page.getByRole("toolbar", { name: /scanner toolbar/i }),
    ).toBeVisible();
  });

  test("renders toolbar + columns via deep-link", async ({ page }) => {
    await page.goto("/#/scanner");
    await page.waitForLoadState("networkidle");
    // Bullish + bearish columns always render (with empty-state text when no
    // cards). Both have an h2 with the direction label.
    await expect(page.locator("text=/Bullish/i").first()).toBeVisible({
      timeout: 10_000,
    });
    await expect(page.locator("text=/Bearish/i").first()).toBeVisible({
      timeout: 10_000,
    });
    // Filter pills + refresh button live in the toolbar.
    await expect(page.getByRole("button", { name: /refresh/i })).toBeVisible();
  });

  test("signal card click deep-links to Tab 1 with symbol+tf query", async ({
    page,
  }) => {
    await page.goto("/#/scanner");
    await page.waitForLoadState("networkidle");

    // Card test ids are `signal-card-<SYMBOL>` (see SignalCard.tsx). On a
    // fresh DB the scanner returns empty arrays — skip in that case.
    const card = page.locator('[data-testid^="signal-card-"]').first();
    const count = await card.count();
    test.skip(count === 0, "no scanner cards available to test deeplink");

    await card.click();
    await expect(page).toHaveURL(/#\/live-prediction\?symbol=.+&tf=.+/);
    // Tab 1 mounts a chart canvas — wait for it to confirm the route worked.
    await page.waitForSelector("canvas", { timeout: 15_000 });
  });

  test("mobile: bullish + bearish columns stack without horizontal scroll", async ({
    page,
    isMobile,
  }) => {
    test.skip(!isMobile, "mobile-only layout assertion");

    await page.goto("/#/scanner");
    await page.waitForLoadState("networkidle");

    const scrollWidth = await page.evaluate(
      () => document.documentElement.scrollWidth,
    );
    const clientWidth = await page.evaluate(
      () => document.documentElement.clientWidth,
    );
    expect(scrollWidth).toBeLessThanOrEqual(clientWidth + 1);

    await expect(page.locator("text=/Bullish/i").first()).toBeVisible();
    await expect(page.locator("text=/Bearish/i").first()).toBeVisible();
  });
});
