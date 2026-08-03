import { expect, test } from "@playwright/test";

import { mockAdminCustomers } from "./admin-customers-fixtures";
import { mockFulfillment } from "./fulfillment-fixtures";
import { mockLeadReports } from "./lead-report-fixtures";
import { mockAdminMFA } from "./mfa-fixtures";

/**
 * Smoke for #155 pool analytics surfaces: Fulfillment breakdown and
 * per-Customer availability / cooldown forecast.
 */

test.describe("Pool analytics surfaces", () => {
  test("Fulfillment shows the cached pool breakdown and refreshes on demand", async ({
    page,
  }) => {
    await mockAdminMFA(page, { assurance: "aal2" });
    await mockFulfillment(page);
    await mockLeadReports(page);

    await page.goto("./admin/fulfillment");

    const section = page.getByRole("region", {
      name: "Pool analytics",
    });
    const dental = section.getByRole("article").filter({ hasText: "TX" })
      .filter({ hasText: "Dental" });
    const unmapped = section.getByRole("article").filter({ hasText: "TX" })
      .filter({ hasText: "unmapped" });
    await expect(dental).toContainText("340 eligible");
    await expect(unmapped).toContainText("18 eligible");
    await expect(section.getByText(/As of:/)).toBeVisible();

    await section.getByRole("button", { name: "Refresh pool breakdown" }).click();
    await expect(dental).toContainText("341 eligible");
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
