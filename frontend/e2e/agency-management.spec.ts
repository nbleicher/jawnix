import { expect, test } from "@playwright/test";

import { mockAdminCustomers } from "./admin-customers-fixtures";
import { mockAdminMFA } from "./mfa-fixtures";

test.beforeEach(async ({ page }) => {
  await mockAdminMFA(page, { assurance: "aal2" });
});

test("previews both directions before permanently changing an assignment", async ({
  page,
}) => {
  const state = await mockAdminCustomers(page);
  await page.goto("./admin/customers/7");

  await page
    .getByRole("button", { name: "Change Agency assignment" })
    .click();
  const dialog = page.getByRole("dialog", {
    name: "Change Agency assignment",
  });
  await dialog.getByLabel("Destination Agency").selectOption("9");
  await dialog.getByRole("button", { name: "Preview consequences" }).click();

  await expect(dialog.getByText("Eligible inventory before")).toBeVisible();
  await expect(dialog.getByText("460")).toBeVisible();
  await expect(dialog.getByText("350")).toBeVisible();
  await expect(
    dialog.getByText(/history merge is permanent/i),
  ).toBeVisible();

  await dialog.getByLabel("Reason").fill("Consolidated servicing ownership.");
  await dialog
    .getByRole("checkbox", {
      name: /shared no-repeat history never splits/i,
    })
    .check();
  await dialog.getByRole("button", { name: "Confirm assignment" }).click();

  await expect(dialog).toBeHidden();
  expect(state.assignmentRequests).toEqual([
    {
      agency_id: 9,
      reason: "Consolidated servicing ownership.",
      confirmed: true,
    },
  ]);
});

test("Agency directory and details stay complete on mobile", async ({
  page,
}) => {
  await mockAdminCustomers(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("./admin/agencies");

  await expect(
    page.getByRole("heading", { level: 1, name: "Agencies" }),
  ).toBeVisible();
  await expect(
    page.getByRole("article", { name: "Gulf Coast Agency" }),
  ).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);

  await page.getByRole("link", { name: "Gulf Coast Agency" }).click();
  await expect(
    page.getByRole("region", { name: "Shared-history impact" }),
  ).toBeVisible();
  await expect(
    page.getByRole("region", { name: "Current members" }),
  ).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
});

test("Agency impact and members use the desktop workspace", async ({ page }) => {
  await mockAdminCustomers(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("./admin/agencies/4");

  const impact = page.getByRole("region", { name: "Shared-history impact" });
  await expect(impact.getByText("Permanent Customers")).toBeVisible();
  await expect(impact.getByText("Merged Agencies")).toBeVisible();
  await expect(impact.getByText("Blocked historical Leads")).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Harbor Insurance" }),
  ).toBeVisible();
});
