import { expect, test } from "@playwright/test";

import { CONFIGURATION_ID, mockAcquisition, SCRAPE_RUN_ID } from "./acquisition-fixtures";
import { mockActivity } from "./activity-fixtures";
import { mockAdminCustomers } from "./admin-customers-fixtures";
import { mockAdminMFA } from "./mfa-fixtures";

test.beforeEach(async ({ page }) => {
  await mockAdminMFA(page, { assurance: "aal2" });
  await mockAdminCustomers(page);
  await mockAcquisition(page);
  await mockActivity(page);
});

test("shows one line per entry with who, what, which record, and why", async ({ page }) => {
  await page.goto("./admin/activity");

  await expect(page.getByRole("heading", { level: 1, name: "Activity" })).toBeVisible();
  const table = page.getByRole("table", { name: "Activity" });
  const failed = table.getByRole("row").filter({ hasText: "Delivery failed" });
  await expect(failed).toContainText("System");
  await expect(failed.getByRole("link", { name: "Batch #1040" })).toHaveAttribute(
    "href",
    "/app/admin/batches/1040",
  );
  await expect(failed).toContainText("Email provider returned 503");
  await expect(table.getByRole("row").filter({ hasText: "Batch requested" }).first()).toContainText(
    "River Morgan",
  );
  await expect(page.getByText("Showing 1–25 of 51")).toBeVisible();
});

test("combines filters and keeps them in the shareable URL", async ({ page }) => {
  await page.goto("./admin/activity");

  await page.getByRole("searchbox", { name: "Search" }).fill("DB1");
  await page.getByRole("combobox", { name: "Who" }).selectOption({ label: "Operator" });
  await page.getByRole("combobox", { name: "Record type" }).selectOption({ label: "Customer" });
  await page.getByLabel("From").fill("2026-11-08");
  await page.getByLabel("Through").fill("2026-11-08");

  await expect(page).toHaveURL(/q=DB1/);
  await expect(page).toHaveURL(/who=operator/);
  await expect(page).toHaveURL(/entityType=customer/);
  await expect(page).toHaveURL(/dateFrom=2026-11-08/);
  await expect(page).toHaveURL(/dateTo=2026-11-08/);
  const rows = page.getByRole("table", { name: "Activity" }).getByRole("row");
  await expect(rows).toHaveCount(2);
  await expect(rows.nth(1)).toContainText("Lead database changed");
});

test("the record type filter offers every recorded type", async ({ page }) => {
  await page.goto("./admin/activity");

  const types = page.getByRole("combobox", { name: "Record type" });
  for (const label of ["Batch", "Customer", "DB2 upload", "Niche mapping", "Nightly review", "Scrape run"]) {
    await expect(types.getByRole("option", { name: label, exact: true })).toHaveCount(1);
  }
});

test("refused only keeps the guarded actions that were refused", async ({ page }) => {
  await page.goto("./admin/activity");

  await page.getByRole("checkbox", { name: "Refused only" }).check();

  await expect(page).toHaveURL(/refused=true/);
  const rows = page.getByRole("table", { name: "Activity" }).getByRole("row");
  await expect(rows).toHaveCount(2);
  await expect(rows.nth(1)).toContainText("Refused: permanent deletion");
  await expect(rows.nth(1)).toContainText("Lakeside Brokers");
});

test("opens an entry to show its before and after", async ({ page }) => {
  await page.goto("./admin/activity");

  await expect(page.getByText("lead_database: db1")).toHaveCount(0);
  await page.getByRole("button", { name: /Lead database changed: show what changed/ }).click();
  await expect(page.getByText("lead_database: db1")).toBeVisible();
  await expect(page.getByText("lead_database: db2")).toBeVisible();
  await page.getByRole("button", { name: /Lead database changed: hide what changed/ }).click();
  await expect(page.getByText("lead_database: db1")).toHaveCount(0);
});

test("pages on the server and keeps the investigation", async ({ page }) => {
  await page.goto("./admin/activity?who=operator");

  await page.getByRole("link", { name: "Next" }).click();

  await expect(page).toHaveURL(/page=2/);
  await expect(page).toHaveURL(/who=operator/);
  await expect(page.getByText(/Showing 26–/)).toBeVisible();
  await page.getByRole("link", { name: "Previous" }).click();
  await expect(page.getByText(/Showing 1–25/)).toBeVisible();
});

test("a record's page carries its own Activity, or says there is none", async ({ page }) => {
  await page.goto(`./admin/acquisition/runs/${SCRAPE_RUN_ID}`);
  await expect(
    page.getByRole("table", { name: "Scrape run activity" }).getByRole("row").filter({
      hasText: "Scrape run committed",
    }),
  ).toContainText("1 segment held");

  await page.goto(`./admin/acquisition/configurations/${CONFIGURATION_ID}`);
  await expect(page.getByRole("region", { name: "Activity" })).toContainText(
    "No consequential action has been recorded for this Scraper Configuration.",
  );
});
