import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

import { mockCustomerAuth } from "./customer-auth-fixtures";
import { mockFeedback } from "./customer-feedback-fixtures";
import {
  BATCH_REQUEST_WORKSPACE,
  mockBatchRequests,
} from "./customer-requests-fixtures";
import {
  ARTIFACT_EXPIRING_ITEM,
  BATCH_READY_ITEM,
  CUSTOMER_OVERVIEW,
  EMPTY_CUSTOMER_OVERVIEW,
  EXPIRING_REQUEST_ID,
  FEEDBACK_NUDGE_ITEM,
  FEEDBACK_REQUEST_ID,
  READY_REQUEST_ID,
  SETUP_PROBLEM_ITEM,
  WAITING_INVENTORY_ITEM,
  WAITING_REQUEST_ID,
} from "./customer-overview-fixtures";

async function openOverview(
  page: Page,
  items: ReadonlyArray<(typeof CUSTOMER_OVERVIEW.items)[number]> =
    CUSTOMER_OVERVIEW.items,
) {
  await mockCustomerAuth(page, { overview: { items } });
  await page.goto("./overview");
  await expect(
    page.getByRole("heading", { level: 1, name: "Overview" }),
  ).toBeVisible();
}

test.describe("Customer Overview attention queue", () => {
  test("shows only actionable items under Opaline", async ({ page }) => {
    await openOverview(page);

    await expect(page.locator("html")).toHaveAttribute("data-theme", "opaline");
    for (const item of CUSTOMER_OVERVIEW.items) {
      await expect(page.getByRole("heading", { name: item.title })).toBeVisible();
    }
    await expect(page.getByRole("list", { name: "Attention queue" })).toBeVisible();
    await expect(page.locator("body")).not.toContainText("Recent deliveries");
    await expect(page.locator("body")).not.toContainText("Current request");
    await expect(page.locator("body")).not.toContainText("waiting_inventory");
  });

  test("batch-ready action downloads the artifact by keyboard", async ({ page }) => {
    await mockCustomerAuth(page, { overview: { items: [BATCH_READY_ITEM] } });
    const state = await mockBatchRequests(page);
    await page.goto("./overview");

    const action = page.getByRole("link", { name: "Download CSV" });
    await action.focus();
    await expect(action).toBeFocused();
    const downloadPromise = page.waitForEvent("download");
    await page.keyboard.press("Enter");
    const download = await downloadPromise;

    expect(download.suggestedFilename()).toBe("requests-customer_batch.csv");
    expect(state.artifactDownloads).toEqual([READY_REQUEST_ID]);
  });

  test("expiring-artifact action downloads the artifact by keyboard", async ({ page }) => {
    await mockCustomerAuth(page, {
      overview: { items: [ARTIFACT_EXPIRING_ITEM] },
    });
    const state = await mockBatchRequests(page);
    await page.goto("./overview");

    const action = page.getByRole("link", { name: "Download CSV" });
    await action.focus();
    const downloadPromise = page.waitForEvent("download");
    await page.keyboard.press("Enter");
    await downloadPromise;

    expect(state.artifactDownloads).toEqual([EXPIRING_REQUEST_ID]);
  });

  test("waiting-inventory action opens the exact request detail", async ({ page }) => {
    await mockCustomerAuth(page, {
      overview: { items: [WAITING_INVENTORY_ITEM] },
    });
    await mockBatchRequests(page, { workspace: BATCH_REQUEST_WORKSPACE });
    await page.goto("./overview");

    const action = page.getByRole("link", { name: "Review request" });
    await action.focus();
    await page.keyboard.press("Enter");

    await expect(page).toHaveURL(
      new RegExp(`/app/requests\\?request=${WAITING_REQUEST_ID}$`),
    );
    await expect(
      page.getByRole("heading", { level: 1, name: "Batch Request" }),
    ).toBeVisible();
  });

  test("feedback nudge opens the feedback flow", async ({ page }) => {
    await mockFeedback(page);
    await mockCustomerAuth(page, { overview: { items: [FEEDBACK_NUDGE_ITEM] } });
    await page.goto("./overview");

    await page.getByRole("link", { name: "Give feedback" }).click();

    await expect(page).toHaveURL(
      new RegExp(`/app/feedback\\?request=${FEEDBACK_REQUEST_ID}$`),
    );
    await expect(
      page.getByRole("heading", { level: 1, name: "Feedback" }),
    ).toBeVisible();
  });

  test("Setup Problem opens Account", async ({ page }) => {
    await openOverview(page, [SETUP_PROBLEM_ITEM]);

    await page.getByRole("link", { name: "Open Account" }).click();

    await expect(page).toHaveURL(/\/app\/account$/);
    await expect(
      page.getByRole("heading", { level: 1, name: "Account" }),
    ).toBeVisible();
  });

  test("is calm and empty when nothing needs the customer", async ({ page }) => {
    await mockCustomerAuth(page, { overview: EMPTY_CUSTOMER_OVERVIEW });
    await page.goto("./overview");

    await expect(
      page.getByRole("heading", { name: "Nothing needs your attention" }),
    ).toBeVisible();
    await expect(page.getByText("You're all caught up.")).toBeVisible();
    await expect(page.getByRole("list", { name: "Attention queue" })).toHaveCount(0);
    await expect(page.getByRole("main").getByRole("link")).toHaveCount(0);
  });

  test("matches the Opaline visual baseline", async ({ page }) => {
    await openOverview(page);

    await expect(page).toHaveScreenshot("customer-overview.png", {
      animations: "disabled",
      fullPage: true,
    });
  });
});
