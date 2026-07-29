import { expect, test } from "@playwright/test";

import { mockAdminMFA } from "./mfa-fixtures";
import { mockOperationsOverview } from "./operations-overview-fixtures";

test.beforeEach(async ({ page }) => {
  await mockAdminMFA(page, { assurance: "aal2" });
});

test.describe("Actionable Operations overview", () => {
  test("identifies every seeded pending action and its real destination", async ({
    page,
  }) => {
    await page.goto("./admin/overview");

    await expect(
      page.getByRole("heading", { level: 1, name: "Overview" }),
    ).toBeVisible();
    await expect(
      page.getByText("8 pending operations identified"),
    ).toBeVisible();

    const work = [
      [
        "Pending Batch Requests",
        "Northstar Insurance",
        "Open Batch Request",
        /\/app\/admin\/fulfillment\/requests\//,
      ],
      [
        "Inventory Conflicts",
        "Older Customer ↔ Newer Customer",
        "Decide Inventory Conflict",
        /\/app\/admin\/fulfillment\/conflicts\//,
      ],
      [
        "Lead Reports",
        "Wrong number report",
        "Review Lead Report",
        /\/app\/admin\/fulfillment\/reports\//,
      ],
      [
        "Eligibility Holds",
        "Lead 2155550199",
        "Review Eligibility Hold",
        /\/app\/admin\/fulfillment\/reports\//,
      ],
      [
        "Delivery failures",
        "Gulfshore Advisors",
        "Recover Delivery",
        /\/app\/admin\/fulfillment\/requests\//,
      ],
      [
        "Failed jobs",
        "Allocate request · Job 17",
        "Open Batch Request",
        /\/app\/admin\/fulfillment\/requests\//,
      ],
      [
        "Scrape Anomalies",
        "Scrape Run 42",
        "Review Scrape Anomaly",
        /\/app\/admin\/acquisition#held-scrape-anomalies$/,
      ],
      [
        "Nightly Reviews",
        "2026-07-29",
        "Review Nightly Review",
        /\/app\/admin\/acquisition#nightly-reviews$/,
      ],
    ] as const;

    for (const [queueName, itemName, actionName, href] of work) {
      const queue = page.getByRole("region", { name: queueName });
      await expect(queue).toBeVisible();
      const item = queue.getByRole("article", { name: itemName });
      await expect(item).toBeVisible();
      await expect(
        item.getByRole("link", { name: actionName }),
      ).toHaveAttribute("href", href);
    }
  });

  test("one failed source leaves every other source actionable", async ({
    page,
  }) => {
    await mockOperationsOverview(page, { unavailable: "fulfillment" });
    await page.goto("./admin/overview");

    await expect(
      page.getByRole("heading", {
        name: "Fulfillment work is temporarily unavailable",
      }),
    ).toBeVisible();
    await expect(
      page.getByRole("article", { name: "Allocate request · Job 17" }),
    ).toBeVisible();
    await expect(
      page.getByRole("article", { name: "Scrape Run 42" }),
    ).toBeVisible();
    await expect(
      page.getByRole("region", { name: "Pending Batch Requests" }),
    ).toHaveCount(0);
    await expect(page.getByText("3 pending operations identified")).toBeVisible();
  });

  test("empty queues explain what will appear and keep workspace actions", async ({
    page,
  }) => {
    await mockOperationsOverview(page, { empty: true });
    await page.goto("./admin/overview");

    await expect(
      page.getByText("0 pending operations identified"),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", {
        name: "No Pending Batch Requests need attention",
      }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", {
        name: "No Failed jobs need attention",
      }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", {
        name: "No Scrape Anomalies need attention",
      }),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: "Open Fulfillment" }),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: "Open Acquisition" }),
    ).toBeVisible();
  });
});
