import { expect, test } from "@playwright/test";

import { ANOMALY_ID, EXCLUSION_ID } from "./acquisition-fixtures";
import { CONFLICT_ID } from "./fulfillment-fixtures";
import { OPEN_REPORT_ID, mockLeadReports } from "./lead-report-fixtures";
import { mockAdminMFA } from "./mfa-fixtures";
import { BATCH_IDS, TODAY, mockOperatorDesk } from "./today-fixtures";
import type { RecordedCall } from "./today-fixtures";

/**
 * Today (P1), the side panel (C), and global search (A) in a real browser,
 * at the Pixel 7 and 1440px desktop viewports.
 */

let calls: RecordedCall[];

test.beforeEach(async ({ page }) => {
  await mockAdminMFA(page, { assurance: "aal2" });
  await mockLeadReports(page);
  calls = await mockOperatorDesk(page);
});

test.describe("Today", () => {
  test("starts with the eight numbers and last night's run", async ({ page }) => {
    await page.goto("./admin/today");

    await expect(page.getByRole("heading", { level: 1, name: "Today" })).toBeVisible();
    const numbers = page.getByLabel("Key numbers");
    await expect(numbers.getByRole("term")).toHaveText([
      "DB1 usable",
      "DB2 usable",
      "Open batches",
      "Delivered · 30 days",
      "Active customers",
      "Credit balance",
      "Credits used · 30 days",
      "Decisions waiting",
    ]);
    for (const text of ["9,183,402", "1,071,288", "2,069,666", "$8,412.50", "$10,348.33", "Oldest 2d"]) {
      await expect(numbers.getByText(text, { exact: true })).toBeVisible();
    }
    const lastNight = page.getByRole("region", { name: "Last night" });
    await expect(lastNight.getByRole("link", { name: "Run 42" })).toBeVisible();
    await expect(lastNight.getByText("+18,420 new leads")).toBeVisible();
    await expect(lastNight.getByText("1 segment held")).toBeVisible();
    await expect(lastNight.getByText(/^Telegram sent \d\d:\d\d$/)).toBeVisible();
    await expect(page.getByRole("region", { name: "Scraper health" })).toHaveText(
      "Scraper running · 6 of 8 workers · queue 812 · last write just now",
    );
  });

  test("links to the Scraper verification when this sign-in has no grant", async ({ page }) => {
    await mockOperatorDesk(page, { scraper: { granted: false } });
    await page.goto("./admin/today");

    const health = page.getByRole("region", { name: "Scraper health" });
    await expect(health).toHaveText("Scraper · verify to see health");
    await health.getByRole("link", { name: "Scraper · verify to see health" }).click();
    await expect(
      page.getByRole("heading", { level: 1, name: "Verify access to Scraper Operations" }),
    ).toBeVisible();
  });

  test("lists every decision once, oldest first", async ({ page }) => {
    await page.goto("./admin/today");

    const decisions = page.getByRole("table", { name: "Decisions" });
    const types = decisions.locator("tbody tr td:first-child");
    await expect(types).toHaveText([
      "Exclusion list",
      "Lead report",
      "Failed job",
      "Inventory conflict",
      "Scrape anomaly",
      "Delivery failed",
      "Approval",
    ]);
    // The lead report carries its hold; the hold is not a second row.
    await expect(decisions.getByText("Wrong number · hold active")).toBeVisible();
    await expect(page.getByLabel("Key numbers").getByText("7", { exact: true })).toBeVisible();
  });

  test("an approval opens beside the list, in the address, and is approved with a reason", async ({ page }) => {
    await page.goto("./admin/today");

    await page.getByRole("table", { name: "Decisions" }).getByRole("link", { name: "Approval" }).click();
    await expect(page).toHaveURL(new RegExp(`decision=approval%3A${BATCH_IDS[1042]}`));
    const panel = page.getByRole("complementary", { name: "#1042 · Northstar Insurance" });
    await expect(panel.getByText("25,000 · FL · DB1")).toBeVisible();
    await expect(panel.getByText("1,188,205")).toBeVisible();
    await expect(panel.getByText("Fillable in full")).toBeVisible();
    await expect(panel.getByText("Ahead once approved")).toBeVisible();
    await expect(panel.getByText("1 batch: #1039 Ridgeline Benefits (waiting)")).toBeVisible();
    await expect(panel.getByText("$125.00 at $5.00 per 1,000")).toBeVisible();
    await expect(panel.getByText("$412.50")).toBeVisible();
    await expect(panel.getByRole("link", { name: "Open full page" })).toHaveAttribute(
      "href",
      "/app/admin/batches/1042",
    );
    // The list stays in view beside the panel at desktop widths.
    await expect(page.getByRole("table", { name: "Decisions" })).toBeVisible();

    await panel.getByRole("button", { name: "Approve" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Reason (required)").fill("Inventory verified.");
    await dialog.getByRole("button", { name: "Approve" }).click();
    await expect
      .poll(() => calls.map((call) => call.url))
      .toContain(`/api/admin/requests/${BATCH_IDS[1042]}/approve`);
    expect(calls.at(-1)?.body).toEqual({ reason: "Inventory verified." });
  });

  test("Escape closes the panel and returns focus to its row", async ({ page }) => {
    await page.goto("./admin/today");

    const trigger = page.getByRole("table", { name: "Decisions" }).getByRole("link", { name: "Inventory conflict" });
    await trigger.click();
    const panel = page.getByRole("complementary", { name: "Ridgeline Benefits ↔ Harbor Insurance" });
    await expect(panel.getByRole("heading", { name: "Ridgeline Benefits ↔ Harbor Insurance" })).toBeFocused();
    await expect(panel.getByRole("link", { name: "#1039" })).toBeVisible();
    await expect(panel.getByRole("button", { name: "Confirm once" })).toBeVisible();

    await page.keyboard.press("Escape");
    await expect(panel).toHaveCount(0);
    await expect(page).not.toHaveURL(/decision=/);
    await expect(trigger).toBeFocused();
  });

  test("a shared address opens the same panel", async ({ page }) => {
    await page.goto(`./admin/today?decision=inventoryConflict%3A${CONFLICT_ID}`);
    const panel = page.getByRole("complementary", { name: "Ridgeline Benefits ↔ Harbor Insurance" });
    await expect(panel.getByText("1,204")).toBeVisible();
    await panel.getByRole("button", { name: "Confirm once" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Reason (required)").fill("Harbor is urgent.");
    await dialog.getByRole("button", { name: "Confirm once" }).click();
    await expect
      .poll(() => calls.map((call) => call.url))
      .toContain(`/api/admin/inventory-conflicts/${CONFLICT_ID}/confirm`);
  });

  test("a delivery failure retries the notification or re-notifies Telegram", async ({ page }) => {
    await page.goto("./admin/today");
    await page.getByRole("table", { name: "Decisions" }).getByRole("link", { name: "Delivery failed" }).click();
    const panel = page.getByRole("complementary", { name: "#1040 · Pinecrest Agency" });
    await expect(panel.getByText("Email provider returned 503")).toBeVisible();
    await expect(panel.getByRole("button", { name: "Retry notification" })).toBeVisible();

    await panel.getByRole("button", { name: "Re-notify in Telegram" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Reason (required)").fill("Message was deleted.");
    await dialog.getByRole("button", { name: "Re-notify" }).click();
    await expect
      .poll(() => calls.map((call) => call.url))
      .toContain(`/api/admin/requests/${BATCH_IDS[1040]}/notify`);
  });

  test("acquisition decisions confirm or deny from the panel", async ({ page }) => {
    await page.goto(`./admin/today?decision=scrapeAnomaly%3A${ANOMALY_ID}`);
    const anomaly = page.getByRole("complementary", { name: "Run 42" });
    await expect(anomaly.getByText("roofing · TX")).toBeVisible();
    await anomaly.getByRole("button", { name: "Deny" }).click();
    let dialog = page.getByRole("dialog");
    await dialog.getByLabel("Reason (required)").fill("Real drop, keep yesterday.");
    await dialog.getByRole("button", { name: "Deny" }).click();
    await expect
      .poll(() => calls.map((call) => call.url))
      .toContain(`/api/admin/scrape-anomalies/${ANOMALY_ID}/deny`);

    await page.goto(`./admin/today?decision=exclusionList%3A${EXCLUSION_ID}`);
    const exclusion = page.getByRole("complementary", { name: "Liberty Roofing" });
    await expect(exclusion.getByText("liberty-dnc.csv")).toBeVisible();
    await exclusion.getByRole("button", { name: "Confirm globally" }).click();
    dialog = page.getByRole("dialog");
    await dialog.getByLabel("Reason (required)").fill("Verified DNC list.");
    await dialog.getByRole("button", { name: "Confirm globally" }).click();
    await expect
      .poll(() => calls.map((call) => call.url))
      .toContain(`/api/admin/exclusion-lists/${EXCLUSION_ID}/confirm`);
  });

  test("a lead report shows its summary and the full page", async ({ page }) => {
    await page.goto(`./admin/today?decision=leadReport%3A${OPEN_REPORT_ID}`);
    const panel = page.getByRole("complementary", { name: "Gulfshore Advisors" });
    await expect(panel.getByText("Wrong number")).toBeVisible();
    await expect(panel.getByRole("link", { name: "Batch #1031" })).toBeVisible();
    await expect(panel.getByRole("link", { name: "Open full page" })).toHaveAttribute(
      "href",
      `/app/admin/fulfillment/reports/${OPEN_REPORT_ID}`,
    );
  });

  test("shows batches in progress, delivered today, and all batches", async ({ page }) => {
    await page.goto("./admin/today");
    const progress = page.getByRole("table", { name: "Batches in progress" });
    await expect(progress.getByRole("link", { name: "#1039" })).toBeVisible();
    await expect(progress.getByText("short 31,590")).toBeVisible();
    const delivered = page.getByRole("table", { name: "Delivered today" });
    await expect(delivered.getByText("Delivery failed")).toBeVisible();
    await expect(delivered.getByText("08:40")).toBeVisible();
    await page.getByRole("link", { name: "All batches (80)" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "All batches" })).toBeVisible();
  });

  test("keeps working when the numbers cannot be read", async ({ page }) => {
    await page.route(/\/api\/admin\/today(\?.*)?$/, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ...TODAY, numbers: null, lastNight: null }),
      }),
    );
    await page.goto("./admin/today");
    await expect(page.getByText(/numbers are temporarily unavailable/)).toBeVisible();
    await expect(page.getByText("No Scrape Run has been committed yet.")).toBeVisible();
    await expect(page.getByRole("table", { name: "Decisions" })).toBeVisible();
  });
});

test.describe("Global search", () => {
  test("groups results and opens one with the keyboard", async ({ page }) => {
    await page.goto("./admin/today");
    const box = page.getByRole("combobox", { name: "Search customers, batches, uploads, and phones" });
    await box.fill("north");
    const results = page.getByRole("listbox", { name: "Search results" });
    await expect(results.getByRole("group", { name: "Customers" })).toBeVisible();
    await expect(results.getByRole("group", { name: "DB2 uploads" })).toBeVisible();
    await expect(results.getByRole("option")).toHaveCount(3);
    await expect(results.getByRole("option").first()).toHaveAttribute("aria-selected", "true");

    await box.press("ArrowDown");
    await expect(results.getByRole("option").nth(1)).toHaveAttribute("aria-selected", "true");
    await box.press("Escape");
    await expect(box).toHaveAttribute("aria-expanded", "false");
    await expect(box).toHaveValue("north");

    // Reopening starts again from the first result.
    await box.press("ArrowDown");
    await expect(box).toHaveAttribute("aria-expanded", "true");
    await expect(results.getByRole("option").first()).toHaveAttribute("aria-selected", "true");
    await box.press("ArrowUp");
    await expect(results.getByRole("option").last()).toHaveAttribute("aria-selected", "true");
    await box.press("ArrowDown");
    await box.press("Enter");
    await expect(page).toHaveURL(/\/app\/admin\/customers\/12$/);
  });

  test("a batch number opens the Batch page", async ({ page }) => {
    await page.goto("./admin/today");
    const box = page.getByRole("combobox", { name: "Search customers, batches, uploads, and phones" });
    await box.fill("1042");
    await expect(page.getByRole("option", { name: /Batch #1042 · Northstar Insurance/ })).toBeVisible();
    await box.press("Enter");
    await expect(page.getByRole("heading", { level: 1, name: "Batch #1042 · Northstar Insurance" })).toBeVisible();
  });

  test("says when nothing matches", async ({ page }) => {
    await page.goto("./admin/today");
    await page.getByRole("combobox").fill("zzz");
    await expect(page.getByText("No customers, batches, uploads, or phones match “zzz”.").first()).toBeVisible();
  });
});
