import { expect, test } from "@playwright/test";

import { mockAcquisition } from "./acquisition-fixtures";
import { mockInsights } from "./insights-fixtures";
import { mockAdminMFA } from "./mfa-fixtures";

/**
 * Insights (P6) in a real browser, at both the Pixel 7 and desktop viewports.
 */

test.beforeEach(async ({ page }) => {
  await mockAdminMFA(page, { assurance: "aal2" });
  await mockAcquisition(page);
  await mockInsights(page);
});

test("the nav reaches Insights, which leads with four 30-day numbers", async ({ page }) => {
  await page.goto("./admin/today");
  const nav = page.getByRole("navigation", { name: "Administration" });
  await nav.getByRole("link", { name: "Insights" }).click();

  await expect(page).toHaveURL(/\/app\/admin\/insights$/);
  await expect(nav.getByRole("link", { name: "Insights" })).toHaveAttribute("aria-current", "page");
  const numbers = page.getByLabel("Last 30 days");
  await expect(numbers).toContainText("$10,348.33");
  await expect(numbers).toContainText("Up 12% on the prior 30 days");
  await expect(numbers).toContainText("$11,500.00");
  await expect(numbers).toContainText("2,069,666");
});

test("revenue, customer trends, and sources read as tables", async ({ page }) => {
  await page.goto("./admin/insights");

  await expect(page.getByRole("table", { name: "Revenue by month" })).toContainText("Nov (9 days)");
  const trends = page.getByRole("table", { name: "Customer trends" });
  await expect(trends.getByText("Short 31,590")).toBeVisible();
  await expect(trends.getByRole("link", { name: "Summit Benefits Group" })).toHaveAttribute(
    "href",
    "/app/admin/customers/1",
  );
  const sources = page.getByRole("table", { name: "Source performance" });
  await expect(sources).toContainText("dentist · TN");
  await expect(page.getByText("Rates stay empty until a segment has outcomes. 1 reported so far.")).toBeVisible();
});

test("all segments open the full Source performance page, and the old address lands there", async ({ page }) => {
  await page.goto("./admin/insights");
  await page.getByRole("link", { name: "All 1,240 segments" }).click();

  await expect(page).toHaveURL(/\/app\/admin\/insights\/sources$/);
  await expect(page.getByRole("heading", { level: 1, name: "Source performance" })).toBeVisible();
  await expect(
    page.getByRole("navigation", { name: "Administration" }).getByRole("link", { name: "Insights" }),
  ).toHaveAttribute("aria-current", "page");

  await page.goto("./admin/acquisition/performance?state=TX");
  await expect(page).toHaveURL(/\/app\/admin\/insights\/sources\?state=TX$/);
});

test("a Telegram nightly review link lands on the nightly reviews", async ({ page }) => {
  await page.goto("./admin/acquisition#nightly-reviews");

  await expect(page).toHaveURL(/\/app\/admin\/insights#nightly-reviews$/);
  const reviews = page.getByRole("region", { name: "Nightly reviews" });
  await expect(reviews).toBeVisible();
  await expect(reviews.getByText("Needs attention")).toBeVisible();
  await expect(reviews.getByText(/Telegram delivery needs reconciling/)).toBeVisible();
});

test("fits the phone without sideways scrolling", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "mobile", "Phone layout only");
  await page.goto("./admin/insights");
  await expect(page.getByRole("heading", { level: 1, name: "Insights" })).toBeVisible();

  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
    await page.evaluate(() => window.innerWidth),
  );
});
