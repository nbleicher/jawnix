import { expect, test } from "@playwright/test";

import { mockAcquisition, SCRAPE_RUN_ID } from "./acquisition-fixtures";
import { mockActivity } from "./activity-fixtures";
import { mockAdminCustomers } from "./admin-customers-fixtures";
import { mockAdminMFA } from "./mfa-fixtures";

test.beforeEach(async ({ page }) => {
  await mockAdminMFA(page, { assurance: "aal2" });
  await mockAdminCustomers(page);
  await mockAcquisition(page);
  await mockActivity(page);
});

test("combines filters and keeps them in the shareable URL", async ({ page }) => {
  await page.goto("./admin/activity");

  await page.getByRole("textbox", { name: "Actor" }).fill(
    "admin.one@example.com",
  );
  await page.getByRole("textbox", { name: "Action" }).fill("customer_updated");
  await page.getByRole("combobox", { name: "Entity type" }).selectOption(
    "customer",
  );
  await page.getByRole("textbox", { name: "Entity ID" }).fill("7");
  await page.getByLabel("From date").fill("2026-07-29");
  await page.getByLabel("Through date").fill("2026-07-29");
  await page.getByRole("button", { name: "Apply filters" }).click();

  await expect(page).toHaveURL(/actor=admin\.one%40example\.com/);
  await expect(page).toHaveURL(/action=customer_updated/);
  await expect(page).toHaveURL(/entityType=customer/);
  await expect(page).toHaveURL(/entityId=7/);
  await expect(page).toHaveURL(/dateFrom=2026-07-29/);
  await expect(page).toHaveURL(/dateTo=2026-07-29/);
  await expect(page.getByRole("article")).toHaveCount(1);
  await expect(
    page.getByRole("heading", { name: "Customer updated" }),
  ).toBeVisible();
});

test("paginates on the server and preserves the investigation", async ({ page }) => {
  await page.goto("./admin/activity");

  await expect(page.getByRole("article")).toHaveCount(25);
  await page.getByRole("link", { name: "Next page" }).click();

  await expect(page).toHaveURL(/page=2/);
  await expect(page.getByText(/entries · page 2 of 3/)).toBeVisible();
  await expect(page.getByRole("article")).toHaveCount(25);
});

test("navigates from an entry to the entity it touched", async ({ page }) => {
  await page.goto(
    "./admin/activity?action=customer_updated&entityType=customer&entityId=7",
  );

  await page.getByRole("link", { name: /Customer 7/ }).click();

  await expect(page).toHaveURL(/\/app\/admin\/customers\/7$/);
  await expect(
    page.getByRole("heading", { level: 1, name: "Harbor Insurance" }),
  ).toBeVisible();
  await expect(
    page.getByRole("region", { name: "Activity" }).getByText(
      "Correct the Customer name from the signed agreement.",
    ),
  ).toBeVisible();
});

test("an entity with no Activity says so plainly", async ({ page }) => {
  await page.goto(`./admin/acquisition/runs/${SCRAPE_RUN_ID}`);

  const activity = page.getByRole("region", { name: "Activity" });
  await expect(
    activity.getByRole("heading", { name: "No activity recorded" }),
  ).toBeVisible();
  await expect(activity).toContainText(
    "No consequential action has been recorded for this Scrape Run.",
  );
});
