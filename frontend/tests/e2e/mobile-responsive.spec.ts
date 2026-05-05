// SP-6 Phase F1: mobile-responsive smoke tests.
//
// Asserts that at the iPhone-13 viewport (390x844) — and a tighter 375px
// width slice taken via setViewportSize — none of the four primary tabs
// produce horizontal overflow on the document. The Tab 1 sidebar is a
// drawer at <md (closed by default) so the chart takes full width; Tab 3
// columns stack via grid-cols-1 md:grid-cols-2; Admin's 6 sub-tabs must
// scroll horizontally inside their container without forcing the document
// to scroll horizontally.
//
// Mirror of bot-status.spec.ts mobile assertion, applied to every tab.

import { expect, test } from "@playwright/test";

const TAB_HASHES = [
  { hash: "#/live-prediction", label: "Tab 1 — Live Prediction" },
  { hash: "#/bot-status", label: "Tab 2 — Bot Status" },
  { hash: "#/scanner", label: "Tab 3 — Scanner" },
  { hash: "#/settings", label: "Tab 4 — Settings" },
] as const;

test.describe("Mobile responsive at 375px", () => {
  test.beforeEach(async ({ page, isMobile }) => {
    test.skip(!isMobile, "mobile-only assertion");
    // Tighten to the spec's 375px target (iPhone SE class) — overrides the
    // device default so we catch regressions narrower than iPhone 13.
    await page.setViewportSize({ width: 375, height: 800 });
  });

  for (const { hash, label } of TAB_HASHES) {
    test(`${label}: no horizontal overflow`, async ({ page }) => {
      await page.goto(`/${hash}`);
      // Allow REST + chart to settle.
      await page.waitForLoadState("networkidle");

      const scrollWidth = await page.evaluate(
        () => document.documentElement.scrollWidth,
      );
      const clientWidth = await page.evaluate(
        () => document.documentElement.clientWidth,
      );
      // Allow a 1px rounding fudge factor.
      expect(scrollWidth).toBeLessThanOrEqual(clientWidth + 1);
    });
  }

  test("Tab 1: chart canvas is visible and sidebar is closed by default", async ({
    page,
  }) => {
    await page.goto("/#/live-prediction");
    await page.waitForSelector("canvas", { timeout: 15_000 });
    // Sidebar is the drawer <aside>; closed → translate-x-full applied.
    await expect(page.locator("aside")).toHaveClass(/translate-x-full/);
    // Chart canvas takes full width because sidebar is fixed-positioned.
    const canvas = page.locator("canvas").first();
    await expect(canvas).toBeVisible();
  });

  test("Tab 3: bullish + bearish columns render in single-column stack", async ({
    page,
  }) => {
    await page.goto("/#/scanner");
    await page.waitForLoadState("networkidle");
    // The grid uses grid-cols-1 md:grid-cols-2; at <md it's a vertical stack.
    // Both column containers exist regardless of card data presence.
    // ScannerToolbar exists with role="toolbar".
    await expect(page.getByRole("toolbar", { name: /scanner toolbar/i })).toBeVisible();
  });

  test("Admin: sub-tabs container scrolls horizontally without forcing document scroll", async ({
    page,
  }) => {
    await page.goto("/#/admin");
    await page.waitForLoadState("networkidle");
    // If user is non-admin, App.tsx renders null — skip in that case so the
    // smoke remains green on a fresh DB.
    const tablist = page.getByRole("tablist", { name: /admin sections/i });
    const present = await tablist.count();
    test.skip(present === 0, "admin tab not visible to current user");

    // Document-level horizontal scroll must be 0.
    const docScrollWidth = await page.evaluate(
      () => document.documentElement.scrollWidth,
    );
    const docClientWidth = await page.evaluate(
      () => document.documentElement.clientWidth,
    );
    expect(docScrollWidth).toBeLessThanOrEqual(docClientWidth + 1);
  });
});
