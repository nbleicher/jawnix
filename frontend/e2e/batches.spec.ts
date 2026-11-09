import { expect, test } from "@playwright/test";

import { mockLeadReports } from "./lead-report-fixtures";
import { mockAdminMFA } from "./mfa-fixtures";
import { BATCH_IDS, mockOperatorDesk } from "./today-fixtures";
import type { RecordedCall } from "./today-fixtures";

/**
 * All batches and the Batch page (P3) at both viewports.
 */

let calls: RecordedCall[];

test.beforeEach(async ({ page }) => {
  await mockAdminMFA(page, { assurance: "aal2" });
  await mockLeadReports(page);
  calls = await mockOperatorDesk(page);
});

test.describe("All batches", () => {
  test("lists open batches oldest first with the operator's columns", async ({ page }) => {
    await page.goto("./admin/batches");

    await expect(page.getByRole("heading", { level: 1, name: "All batches" })).toBeVisible();
    const table = page.getByRole("table", { name: "Batches" });
    await expect(table.locator("tbody tr td:first-child")).toHaveText(["#1039", "#1040", "#1041", "#1042"]);
    await expect(table.getByText("68,410 · short 31,590")).toBeVisible();
    await expect(table.getByText("Delivery failed")).toBeVisible();
    await expect(page.getByRole("link", { name: "Delivered (67)" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Expired files (3)" })).toHaveAttribute(
      "href",
      "/app/admin/batches?status=expired",
    );
  });

  test("filters by what needs a decision, kept in the address", async ({ page }) => {
    await page.goto("./admin/batches");
    await page.getByRole("checkbox", { name: "Needs a decision" }).check();
    await expect(page).toHaveURL(/needsDecision=true/);
    const table = page.getByRole("table", { name: "Batches" });
    await expect(table.locator("tbody tr td:first-child")).toHaveText(["#1040", "#1042"]);
  });

  test("a row opens beside the list and closes back to it", async ({ page }) => {
    await page.goto("./admin/batches");
    const table = page.getByRole("table", { name: "Batches" });
    const trigger = table.getByRole("link", { name: "#1042" });
    await trigger.click();
    await expect(page).toHaveURL(/batch=1042/);
    const panel = page.getByRole("complementary", { name: "#1042 · Northstar Insurance" });
    await expect(panel.getByText("Fillable in full")).toBeVisible();
    await expect(panel.getByRole("button", { name: "Approve" })).toBeVisible();
    await expect(table.locator("tbody tr").nth(3)).toHaveAttribute("aria-current", "true");

    await panel.getByRole("button", { name: "Close panel" }).click();
    await expect(panel).toHaveCount(0);
    await expect(trigger).toBeFocused();
  });
});

test.describe("The Batch page", () => {
  test("puts actions in the header and answers can it be filled and what it costs", async ({ page }) => {
    await page.goto("./admin/batches/1042");

    await expect(
      page.getByRole("heading", { level: 1, name: "Batch #1042 · Northstar Insurance" }),
    ).toBeVisible();
    await expect(page.getByText(/25,000 leads · FL · DB1 · submitted 2 hours ago/)).toBeVisible();
    const fill = page.getByRole("region", { name: "Can it be filled?" });
    await expect(fill.getByText("Fillable in full")).toBeVisible();
    await expect(fill.getByText("1 batch (Ridgeline Benefits, waiting)")).toBeVisible();
    const cost = page.getByRole("region", { name: "Cost" });
    await expect(cost.getByText("$5.00 per 1,000")).toBeVisible();
    await expect(cost.getByText("$125.00, held at submission")).toBeVisible();
    await expect(cost.getByText("$412.50")).toBeVisible();
    await expect(page.getByRole("region", { name: "Batch" }).getByRole("link", { name: "Northstar Insurance" })).toBeVisible();
    await expect(page.getByRole("list", { name: "Batch progress" }).getByRole("listitem")).toHaveCount(5);
    await expect(page.getByRole("region", { name: "Customer's recent batches" }).getByText("#1038 · 50,000 leads")).toBeVisible();

    await page.getByRole("main").getByRole("button", { name: "Reject" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toHaveAccessibleDescription(/Rejection is terminal/);
    await dialog.getByLabel("Reason (required)").fill("Outside appointed states.");
    await dialog.getByRole("button", { name: "Reject" }).click();
    await expect
      .poll(() => calls.map((call) => call.url))
      .toContain(`/api/admin/requests/${BATCH_IDS[1042]}/reject`);
  });

  test("keeps re-notify beside progress for an open batch", async ({ page }) => {
    await page.goto("./admin/batches/1042");
    const progress = page.getByRole("region", { name: "Progress" });
    await progress.getByRole("button", { name: "Re-notify in Telegram" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Reason (required)").fill("Message was deleted.");
    await dialog.getByRole("button", { name: "Re-notify" }).click();
    await expect
      .poll(() => calls.map((call) => call.url))
      .toContain(`/api/admin/requests/${BATCH_IDS[1042]}/notify`);
  });

  test("a failed delivery shows its file and retries the notification", async ({ page }) => {
    await page.goto("./admin/batches/1040");
    await expect(page.getByRole("heading", { level: 1, name: "Batch #1040 · Pinecrest Agency" })).toBeVisible();
    const file = page.getByRole("region", { name: "File" });
    await expect(file.getByText("pinecrest_1040.csv")).toBeVisible();
    await expect(file.getByText("Email provider returned 503")).toBeVisible();
    await expect(page.getByRole("table", { name: "Leads by state" }).getByText("Dental")).toBeVisible();
    await page.getByRole("main").getByRole("button", { name: "Retry notification" }).click();
    await expect(page.getByRole("dialog")).toHaveAccessibleDescription(/exact existing Batch Artifact/);
  });

  test("a delivered batch offers regeneration and no re-notify", async ({ page }) => {
    await page.goto("./admin/batches/1043");
    await expect(page.getByRole("main").getByRole("button", { name: "Regenerate artifact" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Re-notify in Telegram" })).toHaveCount(0);
  });

  test("old Fulfillment request links land here", async ({ page }) => {
    await page.goto(`./admin/fulfillment/requests/${BATCH_IDS[1042]}`);
    await expect(page).toHaveURL(/\/app\/admin\/batches\/1042$/);
    await expect(
      page.getByRole("heading", { level: 1, name: "Batch #1042 · Northstar Insurance" }),
    ).toBeVisible();
  });
});
