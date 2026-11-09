import { expect, test } from "@playwright/test";

import {
  CONFIGURATION_ID,
  EVIDENCE_CHECKSUM,
  RECOMMENDATION_ID,
  mockAcquisition,
} from "./acquisition-fixtures";
import type { RecordedCall } from "./acquisition-fixtures";
import { mockActivity } from "./activity-fixtures";
import { mockAdminMFA } from "./mfa-fixtures";
import { mockSetup } from "./setup-fixtures";

/**
 * Setup (P7) and the Acquisition pieces that moved under it, at both the
 * Pixel 7 and desktop viewports.
 */

let calls: RecordedCall[];

test.beforeEach(async ({ page }) => {
  await mockAdminMFA(page, { assurance: "aal2" });
  calls = await mockAcquisition(page);
  await mockActivity(page);
  await mockSetup(page);
});

test("the nav reaches Setup: one table of occasional tools", async ({ page }) => {
  await page.goto("./admin/today");
  const nav = page.getByRole("navigation", { name: "Administration" });
  await nav.getByRole("link", { name: "Setup" }).click();

  await expect(page).toHaveURL(/\/app\/admin\/setup$/);
  await expect(page.getByRole("heading", { level: 1, name: "Setup" })).toBeVisible();
  await expect(nav.getByRole("link", { name: "Setup" })).toHaveAttribute("aria-current", "page");
  const table = page.getByRole("table", { name: "Setup areas" });
  for (const area of [
    "Scraper workspace",
    "Scraper settings",
    "Configuration versions",
    "Keywords",
    "Niche mappings",
    "Agencies",
    "Security",
    "Billing defaults",
  ]) {
    await expect(table.getByRole("link", { name: area })).toBeVisible();
  }
  await expect(table.getByText(/Verified until you sign out/)).toBeVisible();
  await expect(table.getByText("1 awaiting review")).toBeVisible();
});

test("the old Acquisition address lands on Setup, and Security stays in Setup", async ({ page }) => {
  await page.goto("./admin/acquisition");
  await expect(page).toHaveURL(/\/app\/admin\/setup$/);

  await page.getByRole("table", { name: "Setup areas" }).getByRole("link", { name: "Security" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Administrator security" })).toBeVisible();
  await expect(
    page.getByRole("navigation", { name: "Administration" }).getByRole("link", { name: "Setup" }),
  ).toHaveAttribute("aria-current", "page");
});

test("billing defaults open beside the table", async ({ page }) => {
  await page.goto("./admin/setup");
  await page.getByRole("link", { name: "Billing defaults" }).click();

  await expect(page).toHaveURL(/area=billing/);
  const panel = page.getByRole("complementary", { name: "Billing defaults" });
  await expect(panel).toContainText("$5.00 per 1,000");
  await panel.getByRole("button", { name: "Close panel" }).click();
  await expect(panel).toBeHidden();
});

test("configuration versions list every version and the legacy recommendation's evidence", async ({
  page,
}) => {
  await page.goto("./admin/setup");
  await page.getByRole("link", { name: "Configuration versions" }).click();

  await expect(page.getByRole("heading", { level: 1, name: "Configuration versions" })).toBeVisible();
  const versions = page.getByRole("table", { name: "Scraper Configuration versions" });
  await expect(versions.getByRole("link", { name: "v5" })).toHaveAttribute(
    "href",
    `/app/admin/acquisition/configurations/${CONFIGURATION_ID}`,
  );
  await expect(versions.getByText(/rolled forward from an earlier version/)).toBeVisible();
  await expect(page.getByText(/1 held Scrape Run is waiting/)).toBeVisible();

  await page.getByRole("link", { name: "roof repair · PA" }).click();
  const panel = page.getByRole("complementary", { name: "Reduce · roof repair · PA" });
  await expect(panel).toContainText("120");
  await panel.getByRole("button", { name: "Approve" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toHaveAccessibleDescription(/no existing version is rewritten/);
  await dialog.getByLabel("Reason (required)").fill("Peer evidence supports reducing this segment.");
  await dialog.getByRole("button", { name: "Approve" }).click();

  await expect
    .poll(() => calls.map((call) => call.url))
    .toContain(`/api/admin/source-recommendations/${RECOMMENDATION_ID}/approve`);
  expect(calls.at(-1)?.body["evidenceChecksum"]).toBe(EVIDENCE_CHECKSUM);
});

test("a version page lists its segments and links back to every version", async ({ page }) => {
  await page.goto(`./admin/acquisition/configurations/${CONFIGURATION_ID}`);

  await expect(page.getByRole("heading", { level: 1, name: "Configuration v5" })).toBeVisible();
  await expect(page.getByRole("table", { name: "Source segments" })).toContainText("roofing contractor");
  await expect(page.getByRole("link", { name: "All versions" })).toHaveAttribute(
    "href",
    "/app/admin/setup/configurations",
  );
});

test("Today's niche mapping link opens the mapping, which is decided with a reason", async ({ page }) => {
  await page.goto("./admin/setup/niche-mappings?segment=TX%3A%3Aroofing%20contractor");

  const panel = page.getByRole("complementary", { name: "roofing contractor · TX" });
  await expect(panel).toContainText("Niche mapping · Awaiting review");
  await panel.getByRole("button", { name: "Deny proposal" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Reason (required)").fill("Wrong taxonomy match.");
  await dialog.getByRole("button", { name: "Deny proposal" }).click();

  await expect
    .poll(() => calls.map((call) => call.url))
    .toContain("/api/admin/source-niches/TX%3A%3Aroofing%20contractor/deny");
});

test("an empty reason is refused before anything is sent", async ({ page }) => {
  await page.goto("./admin/setup/niche-mappings?segment=TX%3A%3Aroofing%20contractor");

  await page
    .getByRole("complementary", { name: "roofing contractor · TX" })
    .getByRole("button", { name: "Confirm Niche" })
    .click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "Confirm Niche" }).click();

  await expect(dialog.getByText("A reason is required.")).toBeVisible();
  expect(calls).toHaveLength(0);
});

test("the Scraper workspace rail exits to Setup", async ({ page }) => {
  await page.goto("./admin/acquisition/scraper");

  const exit = page.getByRole("link", { name: "Exit to Setup" });
  await expect(exit).toHaveAttribute("href", "/app/admin/setup");
});
