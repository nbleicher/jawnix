import { expect, test } from "@playwright/test";

import { mockAdminCustomers } from "./admin-customers-fixtures";
import { mockFulfillment } from "./fulfillment-fixtures";
import { mockAdminMFA } from "./mfa-fixtures";

/**
 * Smoke for #155 pool analytics surfaces: the breakdown (on Inventory since
 * A4) and per-Customer availability / cooldown forecast.
 */

test.describe("Pool analytics surfaces", () => {
  test("Inventory's pool analytics shows the cached breakdown and queues a recount on demand", async ({
    page,
  }) => {
    await mockAdminMFA(page, { assurance: "aal2" });
    await mockFulfillment(page);

    await page.goto("./admin/inventory/pool");

    const table = page.getByRole("table", { name: "Pool breakdown" });
    await expect(table.locator("tbody tr").first()).toContainText("TX");
    await expect(table.locator("tbody tr").first()).toContainText("Dental");
    await expect(table.locator("tbody tr").first()).toContainText("340");
    await expect(table.getByRole("row").filter({ hasText: "Unmapped" })).toContainText("18");
    await expect(page.getByText(/^As of /)).toBeVisible();

    await page.getByRole("button", { name: "Refresh breakdown" }).click();
    await expect(page.getByText(/Recount queued/)).toBeVisible();
  });

  test("Customer details shows availability, forecast, and refresh", async ({
    page,
  }) => {
    const state = await mockAdminCustomers(page);
    await page.goto("./admin/customers/7");

    const availability = page.getByRole("region", { name: "Pool availability" });
    await expect(availability.getByText("412", { exact: true })).toBeVisible();
    await expect(
      availability.getByRole("list", { name: "Cooldown forecast" }),
    ).toBeVisible();
    await expect(availability.getByText("2026-08-04: 18")).toBeVisible();

    await availability
      .getByRole("button", { name: "Refresh availability" })
      .click();
    await expect(availability.getByText("420", { exact: true })).toBeVisible();
    expect(state.availabilityRefreshRequests).toBe(1);
  });
});
