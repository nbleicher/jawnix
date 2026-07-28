import { expect, test } from "@playwright/test";

import { mockCustomerAuth } from "./customer-auth-fixtures";
import {
  CUSTOMER_OVERVIEW,
  EMPTY_CUSTOMER_OVERVIEW,
} from "./customer-overview-fixtures";

test.describe("Customer Overview", () => {
  test("summarizes current work through one customer-safe contract", async ({
    page,
  }) => {
    const profileRequests: string[] = [];
    page.on("request", (request) => {
      if (new URL(request.url()).pathname === "/api/me/profile") {
        profileRequests.push(request.url());
      }
    });
    const state = await mockCustomerAuth(page, {
      overview: CUSTOMER_OVERVIEW,
    });

    await page.goto("./overview");

    await expect(
      page.getByRole("heading", { level: 1, name: "Overview" }),
    ).toBeVisible();
    await expect(page.getByText("Preparing Batch", { exact: true })).toBeVisible();
    await expect(page.getByText("750 leads", { exact: true })).toBeVisible();
    await expect(page.getByText("FL, TX", { exact: true })).toBeVisible();
    await expect(page.getByText("500 leads", { exact: true })).toBeVisible();
    await expect(
      page.getByText("There is nothing you need to do.", { exact: false }),
    ).toBeVisible();
    await expect(page.locator("body")).not.toContainText("waiting_inventory");
    await expect(page.locator("body")).not.toContainText("status_message");
    await expect.poll(() => state.overviewRequests).toBe(1);
    expect(profileRequests).toEqual([]);
  });

  test("makes the next action and both primary actions keyboard operable", async ({
    page,
  }) => {
    await mockCustomerAuth(page, { overview: CUSTOMER_OVERVIEW });
    await page.goto("./overview");

    const requestBatch = page.getByRole("link", { name: "Request a Batch" });
    const feedback = page.getByRole("link", { name: "Submit Lead Feedback" });
    const nextAction = page.getByRole("link", { name: "Review Request" });
    await expect(requestBatch).toBeVisible();
    await expect(feedback).toBeVisible();
    await expect(nextAction).toBeVisible();

    await requestBatch.focus();
    await expect(requestBatch).toBeFocused();
    await page.keyboard.press("Enter");

    await expect(page).toHaveURL(/\/app\/requests$/);
    await expect(
      page.getByRole("heading", { level: 1, name: "Requests" }),
    ).toBeVisible();
  });

  test("presents valid nothing-yet states instead of an error", async ({
    page,
  }) => {
    await mockCustomerAuth(page, { overview: EMPTY_CUSTOMER_OVERVIEW });
    await page.goto("./overview");

    await expect(
      page.getByRole("heading", { level: 2, name: "No Batch Requests yet" }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { level: 2, name: "No Licensed States yet" }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { level: 2, name: "No deliveries yet" }),
    ).toBeVisible();
    await expect(page.getByText("Nothing has been added.", { exact: false })).toBeVisible();
    await expect(page.getByText("Nothing has been delivered yet.", { exact: false })).toBeVisible();
    await expect(page.getByRole("alert")).toHaveCount(0);
  });
});
