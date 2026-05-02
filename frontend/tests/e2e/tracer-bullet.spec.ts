import { test, expect } from "@playwright/test";

test("tracer bullet: chart + panels load and update", async ({ page }) => {
  // PLAYWRIGHT_BASE_URL points at the staging deploy in CI; localhost for dev.
  await page.goto("/");

  // Wait for chart canvas
  await page.waitForSelector("canvas", { timeout: 15_000 });

  // Wait for at least one panel value (RSI not "—")
  const rsi = page
    .locator("text=RSI(14)")
    .locator("xpath=..")
    .locator(":scope > :nth-child(2)");
  await expect(rsi).not.toHaveText("—", { timeout: 30_000 });

  // Trade Status appears
  await expect(page.locator("text=Trade Status")).toBeVisible();
});

test("mobile drawer opens and closes", async ({ page, isMobile }) => {
  test.skip(!isMobile, "mobile-only test");

  await page.goto("/");
  await page.click("button[aria-label='Open sidebar']");
  await expect(page.locator("aside")).toBeVisible();
  await page.click("button[aria-label='Close sidebar']");
  await expect(page.locator("aside")).toHaveClass(/translate-x-full/);
});
