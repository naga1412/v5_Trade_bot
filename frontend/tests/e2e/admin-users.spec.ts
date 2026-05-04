// SP-0.7 Phase L5: Playwright E2E for the Admin -> Users sub-tab.
//
// Smoke-grade assertions only — boots the dev stack, navigates to Admin,
// opens the invite modal, fills the form, submits, and verifies the
// step-2 ("Add to Cloudflare Access") panel renders. Mobile + desktop
// projects are inherited from playwright.config.ts.
//
// Selectors are anchored on the actual rendered DOM in:
//   frontend/src/tabs/Admin/Users.tsx        — "+ Invite" button + Panel<Users>
//   frontend/src/tabs/Admin/InviteUserModal.tsx — dialog + form labels +
//                                                 step-2 heading
// They are intentionally permissive (case-insensitive regex) so harmless
// copy edits in the UI do not break the smoke test.

import { expect, test } from "@playwright/test";

test.describe("Admin -> Users", () => {
  test("admin can open invite modal and reach the CF Access step", async ({
    page,
  }) => {
    await page.goto("/");

    // The top-level TabNav exposes one role="tab" per tab; "Admin" is only
    // visible because the dev backend (§4.4) creates dev@local as the
    // bootstrap admin on first hit.
    await page.getByRole("tab", { name: /^admin$/i }).click();
    await expect(page).toHaveURL(/#\/admin/);

    // The Admin tab mounts a sub-nav (also role="tab") with "Users" selected
    // by default. Asserting the Users panel heading is the cheapest signal
    // that the right sub-tree mounted.
    await expect(
      page.getByRole("heading", { name: /^users$/i }),
    ).toBeVisible({ timeout: 10_000 });

    // The invite button label is "+ Invite" (see Users.tsx). Match on
    // /invite/i so a future "+ Invite User" rename does not break the test.
    await page.getByRole("button", { name: /invite/i }).first().click();

    // Modal renders with role="dialog" + aria-label="Invite user".
    const dialog = page.getByRole("dialog", { name: /invite user/i });
    await expect(dialog).toBeVisible();

    // Labels in InviteUserModal: "Email", "Display name", "Notes".
    // getByLabel matches the visible label text; both inputs are required-
    // by-validation but only Email is required by the form.
    const stamp = Date.now();
    await dialog
      .getByLabel(/email/i)
      .fill(`test-friend-${stamp}@example.com`);
    await dialog.getByLabel(/display name/i).fill("Test Friend");

    // Submit button shows "Invite" (or "Inviting…" while in flight). Use a
    // tighter regex anchored on the word boundary so we don't double-match
    // the modal title or the page-level "+ Invite" button.
    await dialog.getByRole("button", { name: /^invite$/i }).click();

    // Step 2: heading reads "Step 2 — Add to Cloudflare Access". We assert
    // on the substring so an em-dash vs hyphen edit doesn't false-fail.
    await expect(
      page.getByText(/add to cloudflare access/i),
    ).toBeVisible({ timeout: 10_000 });

    // Step 2 also shows the email back in a copy-able code block — sanity
    // check that the value round-tripped through the API.
    await expect(
      page.locator(`text=test-friend-${stamp}@example.com`),
    ).toBeVisible();
  });
});
