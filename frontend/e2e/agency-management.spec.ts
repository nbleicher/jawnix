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
  await expect(
    page.getByRole("link", { name: /Harbor Insurance/ }),
  ).toBeVisible();
  await expect(
    page.getByRole("article", { name: "Independent Customers" }),
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

test("opens and manages the floating Customer card", async ({ page }) => {
  const state = await mockAdminCustomers(page);
  await page.goto("./admin/agencies");

  await page.getByRole("link", { name: /Harbor Insurance/ }).click();
  await expect(page).toHaveURL(/\/admin\/agencies\?customer=7$/);
  const customer = page.getByRole("dialog", { name: "Harbor Insurance" });
  await expect(customer).toBeVisible();
  await expect(
    customer.getByRole("link", { name: "Open full record" }),
  ).toBeVisible();

  await customer.getByRole("button", { name: "Send password reset" }).click();
  const reset = page.getByRole("dialog", { name: "Send password reset" });
  await reset.getByRole("button", { name: "Send password reset" }).click();
  await expect(reset).toBeHidden();
  expect(state.passwordResetRequests).toHaveLength(1);

  await customer.getByRole("button", { name: "Rename Customer" }).click();
  const rename = page.getByRole("dialog", { name: "Rename Customer" });
  await rename.getByLabel("Customer name").fill("Harbor Group");
  await rename.getByLabel("Reason").fill("Legal name changed");
  await rename.getByRole("button", { name: "Rename Customer" }).click();
  await expect(rename).toBeHidden();
  expect(state.customerPatchRequests).toContainEqual({
    name: "Harbor Group",
    agency_id: 4,
    active: true,
    reason: "Legal name changed",
  });

  await customer.getByRole("button", { name: "Close dialog" }).click();
  await expect(page).toHaveURL(/\/admin\/agencies$/);
});

test("opens a floating Customer card from a deep link", async ({ page }) => {
  await mockAdminCustomers(page);
  await page.goto("./admin/agencies?customer=7");

  await expect(
    page.getByRole("dialog", { name: "Harbor Insurance" }),
  ).toBeVisible();
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
