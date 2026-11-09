import { expect, test } from "@playwright/test";

import { mockActivity } from "./activity-fixtures";
import { mockAdminCustomers } from "./admin-customers-fixtures";
import { mockAdminMFA } from "./mfa-fixtures";
import { mockAgencies } from "./setup-fixtures";

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

test("Agencies are one table with members, deliveries, and open batches", async ({ page }) => {
  await mockAdminCustomers(page);
  await mockAgencies(page);
  await page.goto("./admin/agencies");

  await expect(page.getByRole("heading", { level: 1, name: "Agencies" })).toBeVisible();
  const agencies = page.getByRole("table", { name: "Agencies" });
  const gulf = agencies.getByRole("row").filter({ hasText: "Gulf Coast Agency" });
  await expect(gulf).toContainText("375,000");
  await expect(gulf).toContainText("1,240,000");
  const members = page.getByRole("table", { name: "Agency members" });
  await expect(members.getByRole("link", { name: "Harbor Insurance" })).toHaveAttribute(
    "href",
    "/app/admin/customers/7",
  );
  await expect(
    page.getByRole("table", { name: "Independent customers" }).getByRole("link", { name: "Summit Benefits Group" }),
  ).toBeVisible();
  await expect(
    page.getByRole("navigation", { name: "Administration" }).getByRole("link", { name: "Setup" }),
  ).toHaveAttribute("aria-current", "page");
});

test("search and status filter the table from the URL", async ({ page }) => {
  await mockAdminCustomers(page);
  await mockAgencies(page);
  await page.goto("./admin/agencies");

  await page.getByRole("searchbox", { name: "Search" }).fill("ridgeline");
  await expect(page).toHaveURL(/q=ridgeline/);
  const agencies = page.getByRole("table", { name: "Agencies" });
  await expect(agencies.getByRole("row")).toHaveCount(2);
  await expect(agencies).toContainText("Lakeside Agency");
});

test("an Agency's page links its members and keeps its history", async ({ page }) => {
  await mockAdminCustomers(page);
  await mockAgencies(page);
  await mockActivity(page);
  await page.goto("./admin/agencies");
  await page.getByRole("table", { name: "Agencies" }).getByRole("link", { name: "Gulf Coast Agency" }).click();

  await expect(page.getByRole("heading", { level: 1, name: "Gulf Coast Agency" })).toBeVisible();
  await expect(page.getByLabel("Agency numbers")).toContainText("1,240,000");
  await expect(
    page.getByRole("table", { name: "Members", exact: true }).getByRole("link", { name: "Pinecrest Agency" }),
  ).toHaveAttribute("href", "/app/admin/customers/8");
  const history = page.getByRole("table", { name: "Membership history" });
  await expect(history).toContainText("Consolidated servicing ownership.");
  await expect(history).toContainText("System");
  await expect(page.getByRole("table", { name: "Agency activity" })).toContainText("Agency updated");
  await expect(page.getByRole("button", { name: "Delete Agency" })).toBeDisabled();
});

test("Agencies fit the phone without sideways scrolling", async ({ page }) => {
  await mockAdminCustomers(page);
  await mockAgencies(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("./admin/agencies");
  await expect(page.getByRole("table", { name: "Agencies" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);

  await page.goto("./admin/agencies/4");
  await expect(page.getByRole("table", { name: "Members", exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});
