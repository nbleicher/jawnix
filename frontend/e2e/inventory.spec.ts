import { expect, test } from "@playwright/test";

import {
  FAILED_UPLOAD_ID,
  LOOKUP_LEAD_ID,
  READY_UPLOAD_ID,
  SUPPRESSED_ID,
  WAITING_LIST_ID,
  mockInventory,
} from "./inventory-fixtures";
import type { RecordedCall } from "./inventory-fixtures";
import { mockAdminMFA } from "./mfa-fixtures";
import { mockOperatorDesk } from "./today-fixtures";

/**
 * Inventory (P4), its tools, lead lookup (P5), and phone search in a real
 * browser, at the Pixel 7 and 1440px desktop viewports.
 */

let calls: RecordedCall[];

test.beforeEach(async ({ page }) => {
  await mockAdminMFA(page, { assurance: "aal2" });
  calls = await mockInventory(page);
});

test.describe("Inventory", () => {
  test("is the Inventory destination and starts with both databases", async ({ page }) => {
    await page.goto("./admin/today");
    const nav = page.getByRole("navigation", { name: "Administration" });
    await expect(nav.getByRole("link", { name: "Inventory" })).toHaveAttribute("href", "/app/admin/inventory");
    await nav.getByRole("link", { name: "Inventory" }).click();

    await expect(page.getByRole("heading", { level: 1, name: "Inventory" })).toBeVisible();
    await expect(nav.getByRole("link", { name: "Inventory" })).toHaveAttribute("aria-current", "page");
    const db1 = page.getByRole("region", { name: "DB1 · Operator leads" });
    await expect(db1.getByText("9,183,402 usable")).toBeVisible();
    await expect(db1.getByText(/^As of \d\d:\d\d$/)).toBeVisible();
    for (const text of ["9,244,326", "−42,612", "−18", "−18,294", "+96,140"]) {
      await expect(db1.getByText(text, { exact: true })).toBeVisible();
    }
    const db2 = page.getByRole("region", { name: "DB2 · CSV uploads" });
    await expect(db2.getByText("1,071,288 usable")).toBeVisible();
    await expect(db2.getByText(/by Alex$/)).toBeVisible();
  });

  test("lists usable leads by state, ten first, with totals and a CSV", async ({ page }) => {
    await page.goto("./admin/inventory");
    const table = page.getByRole("table", { name: "Usable by state" });
    await expect(table.locator("tbody tr")).toHaveCount(10);
    await expect(table.locator("tbody tr td:first-child")).toHaveText(
      ["TX", "FL", "CA", "PA", "OH", /.+/, /.+/, /.+/, /.+/, /.+/],
    );
    await expect(table.locator("tfoot")).toContainText("All 51 states");
    await expect(table.locator("tfoot")).toContainText("10,254,690");
    await expect(page.getByRole("link", { name: "Download CSV" })).toHaveAttribute(
      "href",
      "/api/admin/inventory/usable-by-state.csv",
    );
    await page.getByRole("button", { name: "Show all 51 states" }).click();
    await expect(table.locator("tbody tr")).toHaveCount(51);
  });

  test("queues a recount from the header", async ({ page }) => {
    await page.goto("./admin/inventory");
    await page.getByRole("button", { name: "Refresh counts" }).click();
    await expect(page.getByText("Recount queued. The numbers update when it finishes.")).toBeVisible();
    expect(calls.map((call) => call.url)).toContain("/api/admin/inventory-snapshots/refresh");
  });

  test("reaches every inventory tool", async ({ page }) => {
    await page.goto("./admin/inventory");
    const tools = page.getByRole("navigation", { name: "Inventory tools" });
    await expect(tools.getByRole("link")).toHaveText([
      "DB2 uploads (14)",
      "Exclusion lists (1 waiting)",
      "Suppressed leads (42,612)",
      "Niche assignment",
      "Pool analytics",
    ]);
    await tools.getByRole("link", { name: "Suppressed leads (42,612)" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Suppressed leads" })).toBeVisible();
  });
});

test.describe("Lead lookup", () => {
  test("opens from the Inventory box with everything about the number", async ({ page }) => {
    await page.goto("./admin/inventory");
    await page.getByRole("search", { name: "Lead lookup" }).getByLabel("Phone number").fill("(615) 555-0142");
    await page.getByRole("button", { name: "Look up" }).click();

    await expect(page).toHaveURL(/\/app\/admin\/inventory\/lookup\?phone=/);
    await expect(page.getByRole("heading", { level: 1, name: "Lead lookup" })).toBeVisible();
    const summary = page.getByLabel("Lead summary");
    await expect(summary.getByRole("term")).toHaveText(["Lead", "Usable", "Databases", "Deliveries"]);
    await expect(summary.getByText("(615) 555-0142")).toBeVisible();
    await expect(summary.getByText("Nashville Family Dental · TN · dentist")).toBeVisible();
    await expect(summary.getByText("Not suppressed · no hold · not excluded")).toBeVisible();
    await expect(summary.getByText("DB1 + DB2")).toBeVisible();

    const deliveries = page.getByRole("table", { name: "Deliveries" });
    await expect(deliveries.getByRole("link", { name: "Keystone Medicare" })).toHaveAttribute("href", "/app/admin/customers/4");
    await expect(deliveries.getByRole("link", { name: "#1028" })).toHaveAttribute("href", "/app/admin/batches/1028");
    await expect(deliveries.getByText("Positive response")).toBeVisible();
    await expect(page.getByRole("table", { name: "Listing history" }).getByText("Run 39 · dentist · TN")).toBeVisible();

    const next = page.getByRole("region", { name: "Who can receive it next" });
    await expect(next.getByText("Gulf Coast Agency members (2)")).toBeVisible();
    await expect(next.getByText("Never · shared history").first()).toBeVisible();
    await expect(next.getByText(/^From\s[A-Z][a-z]{2}\s\d{1,2}/)).toBeVisible();
    await expect(page.getByText("No reports, holds, suppressions, corrections, or exclusion lists.")).toBeVisible();
  });

  test("corrects and suppresses, each with a reason", async ({ page }) => {
    await page.goto("./admin/inventory/lookup?phone=6155550142");
    const actions = page.getByRole("region", { name: "Actions" });

    await actions.getByRole("button", { name: "Correct title or state" }).click();
    let dialog = page.getByRole("dialog");
    await expect(dialog.getByText("Current Listing (Google Maps observation)")).toBeVisible();
    await dialog.getByLabel("Corrected title").fill("Nashville Dental");
    await dialog.getByLabel("Reason (required)").fill("Renamed in October.");
    await dialog.getByRole("button", { name: "Correct title or state" }).click();
    await expect.poll(() => calls.map((call) => `${call.method} ${call.url}`)).toContain(
      `PUT /api/admin/leads/${LOOKUP_LEAD_ID}/correction`,
    );
    expect(calls.at(-1)?.body).toEqual({ reason: "Renamed in October.", title: "Nashville Dental" });

    await actions.getByRole("button", { name: "Suppress" }).click();
    dialog = page.getByRole("dialog");
    await dialog.getByRole("button", { name: "Suppress" }).click();
    await expect(dialog.getByText("A reason is required.")).toBeVisible();
    await dialog.getByLabel("Reason (required)").fill("Disconnected number.");
    await dialog.getByRole("button", { name: "Suppress" }).click();
    await expect.poll(() => calls.map((call) => `${call.method} ${call.url}`)).toContain(
      `PUT /api/admin/leads/${LOOKUP_LEAD_ID}/suppression`,
    );
  });

  test("says when no lead has the number", async ({ page }) => {
    await page.goto("./admin/inventory/lookup?phone=212-555-0100");
    await expect(page.getByText("No lead in DB1 or DB2 has the number (212) 555-0100.")).toBeVisible();
    await expect(page.getByLabel("Phone number")).toHaveValue("212-555-0100");
  });

  test("Activity's Lead links open the same lookup", async ({ page }) => {
    await page.goto(`./admin/inventory/lookup?lead=${LOOKUP_LEAD_ID}`);
    await expect(page.getByLabel("Lead summary").getByText("(615) 555-0142")).toBeVisible();
  });
});

test.describe("Phone search", () => {
  test("a phone number in the header search opens lead lookup", async ({ page }) => {
    await page.goto("./admin/inventory");
    const box = page.getByRole("banner").getByRole("combobox", { name: "Search customers, batches, uploads, and phones" });
    await box.fill("615 555 0142");
    const results = page.getByRole("listbox", { name: "Search results" });
    await expect(results.getByRole("group", { name: "Lead" })).toBeVisible();
    await expect(results.getByRole("group", { name: "Delivered in" }).getByRole("option")).toHaveCount(2);
    await expect(results.getByRole("group", { name: "Uploaded in" })).toBeVisible();
    await box.press("Enter");
    await expect(page).toHaveURL(/\/app\/admin\/inventory\/lookup\?phone=6155550142$/);
    await expect(page.getByLabel("Lead summary").getByText("DB1 + DB2")).toBeVisible();
  });
});

test.describe("DB2 uploads", () => {
  test("the old address lands on Inventory's uploads page", async ({ page }) => {
    await page.goto("./admin/lead-uploads");
    await expect(page).toHaveURL(/\/app\/admin\/inventory\/uploads$/);
    await expect(page.getByRole("heading", { level: 1, name: "DB2 uploads" })).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Administration" }).getByRole("link", { name: "Inventory" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  test("lists every upload with its uploader and one action per row", async ({ page }) => {
    await page.goto("./admin/inventory/uploads");
    const table = page.getByRole("table", { name: "DB2 uploads" });
    await expect(table.locator("tbody tr")).toHaveCount(3);
    await expect(table.getByText("Alex")).toBeVisible();
    await expect(table.getByRole("link", { name: "Download rejected rows" })).toHaveAttribute(
      "href",
      `/api/admin/lead-uploads/${READY_UPLOAD_ID}/rejected`,
    );
    await table.getByRole("button", { name: "Retry import" }).click();
    await expect.poll(() => calls.map((call) => call.url)).toContain(`/api/admin/lead-uploads/${FAILED_UPLOAD_ID}/retry`);
    await expect(table.getByText("Queued")).toBeVisible();
  });

  test("uploads a CSV for a chosen state and follows its import to Ready", async ({ page }) => {
    await page.goto("./admin/inventory/uploads");
    const form = page.getByRole("form", { name: "New DB2 upload" });
    await form.getByLabel("CSV file (required)").setInputFiles({
      name: "new.csv",
      mimeType: "text/csv",
      buffer: Buffer.from("phone,name\n6155550199,Example\n"),
    });
    await form.getByLabel("State (required)").selectOption("TN");
    await form.getByRole("button", { name: "Upload CSV" }).click();
    const row = page.getByRole("table", { name: "DB2 uploads" }).getByRole("row").filter({ hasText: "new.csv" });
    await expect(row).toBeVisible();
    await expect(row.getByText("Queued")).toBeVisible();
    await expect(row.getByText("Operator")).toBeVisible();
    const submitted = calls.find((call) => call.method === "POST" && call.url === "/api/admin/lead-uploads");
    expect(String(submitted?.body)).toContain("6155550199,Example");
    expect(String(submitted?.body)).toMatch(/name="state"\r\n\r\nTN/);
    // Importing rows are re-read every five seconds until they settle.
    await expect(row.getByText("Ready")).toBeVisible({ timeout: 12_000 });
    await expect(form.getByLabel("CSV file (required)")).toHaveValue("");
  });

  test("takes a CSV dropped onto the form", async ({ page }) => {
    await page.goto("./admin/inventory/uploads");
    const form = page.getByRole("form", { name: "New DB2 upload" });
    const transfer = await page.evaluateHandle(() => {
      const data = new DataTransfer();
      data.items.add(new File(["Phone,Name\n6155550100,Dropped fixture\n"], "dropped.csv", { type: "text/csv" }));
      return data;
    });
    await form.locator(".jx-upload-drop").dispatchEvent("drop", { dataTransfer: transfer });
    await expect(form.getByText("dropped.csv · 0.0 MB", { exact: true })).toBeVisible();
    await form.getByLabel("State (required)").selectOption("FL");
    await form.getByRole("button", { name: "Upload CSV" }).click();
    await expect(page.getByRole("table", { name: "DB2 uploads" }).getByText("dropped.csv")).toBeVisible();
    const submitted = calls.find((call) => call.method === "POST" && call.url === "/api/admin/lead-uploads");
    expect(String(submitted?.body)).toContain('filename="dropped.csv"');
    expect(String(submitted?.body)).toContain("Dropped fixture");
    await transfer.dispose();
  });
});

test.describe("Exclusion lists", () => {
  test("a waiting Customer list opens beside the list and is confirmed with a reason", async ({ page }) => {
    await page.goto("./admin/inventory/exclusions");
    const waiting = page.getByRole("table", { name: "Waiting for a decision" });
    await waiting.getByRole("link", { name: "Liberty Roofing" }).click();
    await expect(page).toHaveURL(new RegExp(`list=${WAITING_LIST_ID}`));
    const panel = page.getByRole("complementary", { name: "Liberty Roofing" });
    await expect(panel.getByText("liberty-dnc.csv")).toBeVisible();
    await panel.getByRole("button", { name: "Confirm globally" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Reason (required)").fill("Verified the DNC source.");
    await dialog.getByRole("button", { name: "Confirm globally" }).click();
    await expect.poll(() => calls.map((call) => call.url)).toContain(`/api/admin/exclusion-lists/${WAITING_LIST_ID}/confirm`);
  });

  test("lists every list and takes a global bulk upload", async ({ page }) => {
    await page.goto("./admin/inventory/exclusions");
    const all = page.getByRole("table", { name: "All exclusion lists" });
    await expect(all.getByText("Customer only")).toBeVisible();
    await expect(all.getByText("Global", { exact: true })).toBeVisible();

    const form = page.getByRole("form", { name: "Bulk upload" });
    await form.getByLabel("CSV file (required)").setInputFiles({
      name: "dnc.csv",
      mimeType: "text/csv",
      buffer: Buffer.from("phone\n6155550142\n"),
    });
    await form.getByLabel("Reason (required)").fill("Quarterly DNC refresh.");
    await form.getByRole("button", { name: "Upload globally" }).click();
    await expect.poll(() => calls.map((call) => `${call.method} ${call.url}`)).toContain("POST /api/admin/exclusion-lists");
  });
});

test.describe("Suppressed leads, Niche assignment, and pool analytics", () => {
  test("a suppressed lead is restored with a reason", async ({ page }) => {
    await page.goto("./admin/inventory/suppressed");
    const table = page.getByRole("table", { name: "Suppressed leads" });
    await expect(table.getByRole("link", { name: "(512) 555-0987" })).toHaveAttribute(
      "href",
      "/app/admin/inventory/lookup?phone=5125550987",
    );
    await expect(page.getByText(/Restoring eligibility returns this Lead to the ordinary allocation rules/).first()).toBeVisible();
    await table.getByRole("button", { name: "Restore eligibility" }).first().click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Reason (required)").fill("Report was mistaken.");
    await dialog.getByRole("button", { name: "Restore eligibility" }).click();
    await expect.poll(() => calls.map((call) => `${call.method} ${call.url}`)).toContain(
      `DELETE /api/admin/leads/${SUPPRESSED_ID}/suppression`,
    );
  });

  test("Niche assignment exports and imports a CSV", async ({ page }) => {
    await page.goto("./admin/inventory/niche-assignment");
    await expect(page.getByRole("link", { name: "Export unmapped inventory" })).toHaveAttribute(
      "href",
      "/api/admin/niche-assignments/export",
    );
    await page.getByLabel("Niche Assignment CSV (required)").setInputFiles({
      name: "niches.csv",
      mimeType: "text/csv",
      buffer: Buffer.from("phone,niche\n6155550142,dentist\n"),
    });
    await page.getByRole("button", { name: "Import assignments" }).click();
    await expect(page.getByLabel("Niche Assignment import status").getByText("1,150")).toBeVisible();
    expect(calls.map((call) => call.url)).toContain("/api/admin/niche-assignments");
  });

  test("pool analytics lists usable DB1 leads by state and Niche", async ({ page }) => {
    await page.goto("./admin/inventory/pool");
    const table = page.getByRole("table", { name: "Pool breakdown" });
    await expect(table.locator("tbody tr").first()).toContainText("FL");
    await expect(table.locator("tbody tr").first()).toContainText("410,002");
    await expect(table.getByText("Unmapped")).toBeVisible();
    await page.getByLabel("State").selectOption("TX");
    await expect(table.locator("tbody tr")).toHaveCount(2);
    await page.getByRole("button", { name: "Refresh breakdown" }).click();
    await expect(page.getByText("Recount queued. The breakdown updates when it finishes.")).toBeVisible();
  });
});

test.describe("Fulfillment", () => {
  test("the old Fulfillment address lands on All batches", async ({ page }) => {
    await mockOperatorDesk(page);
    await page.goto("./admin/fulfillment");
    await expect(page).toHaveURL(/\/app\/admin\/batches$/);
    await expect(page.getByRole("heading", { level: 1, name: "All batches" })).toBeVisible();
  });
});
