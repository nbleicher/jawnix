import { expect, test } from "@playwright/test";

import {
  CONFLICT_ID,
  DELIVERED_REQUEST_ID,
  EXPIRED_ARTIFACT_ID,
  FAILED_WITHOUT_ARTIFACT_ID,
  FAILED_WITH_ARTIFACT_ID,
  PENDING_REQUEST_ID,
  mockFulfillment,
} from "./fulfillment-fixtures";
import { mockLeadReports } from "./lead-report-fixtures";
import { mockAdminMFA } from "./mfa-fixtures";
import type { RecordedCall } from "./fulfillment-fixtures";

/**
 * Core Fulfillment operations in a real browser (#57).
 *
 * Both Playwright projects run these, so every assertion holds at the Pixel 7
 * and desktop viewports — the workspace has to stay complete and operable on
 * mobile, not merely fit.
 */

let calls: RecordedCall[];

/** The same Batch Request after someone approved it somewhere else. */
const STALE_APPROVED_DETAIL = {
  id: PENDING_REQUEST_ID,
  customer: "Dana Reed",
  customerIdentity: "Northstar Insurance",
  agency: "Northstar Agency",
  email: "dana@example.com",
  leadCount: 250,
  states: ["TX"],
  stateMode: "all_saved",
  status: "approved",
  statusMessage: "Approved; allocation is queued.",
  availableCount: null,
  createdAt: "2026-07-01T10:00:00Z",
  actions: [],
  approvedAt: "2026-07-01T11:00:00Z",
  deliveredAt: null,
  artifact: null,
  distribution: { committed: 0, expected: 250, complete: false },
  telegram: { decisionPending: false },
  history: [],
};

test.beforeEach(async ({ page }) => {
  await mockAdminMFA(page, { assurance: "aal2" });
  calls = await mockFulfillment(page);
  // The workspace now carries the #58 sections too; their controls must not
  // fall through to an unmocked endpoint from these specs.
  await mockLeadReports(page);
});

test.describe("Only domain-valid actions are offered", () => {
  test("a pending Batch Request offers approve, reject, and cancel", async ({
    page,
  }) => {
    await page.goto(`./admin/fulfillment/requests/${PENDING_REQUEST_ID}`);

    const actions = page.getByRole("region", { name: "Actions" });
    await expect(actions.getByRole("button", { name: "Approve" })).toBeVisible();
    await expect(actions.getByRole("button", { name: "Reject" })).toBeVisible();
    await expect(actions.getByRole("button", { name: "Cancel" })).toBeVisible();
    await expect(
      actions.getByRole("button", { name: "Retry notification" }),
    ).toHaveCount(0);
    await expect(
      actions.getByRole("button", { name: "Retry generation" }),
    ).toHaveCount(0);
  });

  test("a delivered Batch Request offers nothing and says so", async ({
    page,
  }) => {
    await page.goto(`./admin/fulfillment/requests/${DELIVERED_REQUEST_ID}`);

    const actions = page.getByRole("region", { name: "Actions" });
    await expect(
      actions.getByText(
        "This Batch Request has reached a state with no valid actions.",
      ),
    ).toBeVisible();
    for (const label of ["Approve", "Reject", "Cancel", "Retry notification"]) {
      await expect(actions.getByRole("button", { name: label })).toHaveCount(0);
    }
  });
});

test.describe("Delivery recovery keeps its two paths apart", () => {
  test("a failed request with an artifact retries the exact artifact", async ({
    page,
  }) => {
    await page.goto(`./admin/fulfillment/requests/${FAILED_WITH_ARTIFACT_ID}`);

    const actions = page.getByRole("region", { name: "Actions" });
    await expect(
      actions.getByRole("button", { name: "Retry notification" }),
    ).toBeVisible();
    // Reallocating would consume more inventory for permanent Distribution
    // Events, so it must not be on offer here.
    await expect(
      actions.getByRole("button", { name: "Retry generation" }),
    ).toHaveCount(0);

    await actions.getByRole("button", { name: "Retry notification" }).click();
    await expect(page.getByRole("dialog")).toHaveAccessibleDescription(
      /Emails another portal notification for the exact existing Batch Artifact/,
    );
  });

  test("a failed request without an artifact retries generation", async ({
    page,
  }) => {
    await page.goto(
      `./admin/fulfillment/requests/${FAILED_WITHOUT_ARTIFACT_ID}`,
    );

    const actions = page.getByRole("region", { name: "Actions" });
    await expect(
      actions.getByRole("button", { name: "Retry generation" }),
    ).toBeVisible();
    await expect(
      actions.getByRole("button", { name: "Retry notification" }),
    ).toHaveCount(0);

    await actions.getByRole("button", { name: "Retry generation" }).click();
    await expect(page.getByRole("dialog")).toHaveAccessibleDescription(
      /runs allocation again/,
    );
  });
});

test.describe("Consequential actions are confirmed with a reason", () => {
  test("the reason reaches the server and the dialog states the consequence", async ({
    page,
  }) => {
    await page.goto(`./admin/fulfillment/requests/${PENDING_REQUEST_ID}`);

    await page
      .getByRole("region", { name: "Actions" })
      .getByRole("button", { name: "Approve" })
      .click();

    const dialog = page.getByRole("dialog");
    await expect(dialog).toHaveAccessibleName("Approve");
    await expect(dialog).toHaveAccessibleDescription(
      /Authorizes this Batch Request's first allocation attempt/,
    );

    await dialog.getByLabel("Reason (required)").fill("Inventory verified.");
    await dialog.getByRole("button", { name: "Approve" }).click();

    await expect
      .poll(() => calls.map((call) => call.url))
      .toContain(`/api/admin/requests/${PENDING_REQUEST_ID}/approve`);
    expect(calls.at(-1)?.reason).toBe("Inventory verified.");
  });

  test("an empty reason is refused before anything is sent", async ({
    page,
  }) => {
    await page.goto(`./admin/fulfillment/requests/${PENDING_REQUEST_ID}`);

    await page
      .getByRole("region", { name: "Actions" })
      .getByRole("button", { name: "Approve" })
      .click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("button", { name: "Approve" }).click();

    await expect(dialog.getByText("A reason is required.")).toBeVisible();
    expect(calls).toHaveLength(0);
  });

  test("a destructive action survives a backdrop click", async ({ page }) => {
    await page.goto(`./admin/fulfillment/requests/${PENDING_REQUEST_ID}`);

    await page
      .getByRole("region", { name: "Actions" })
      .getByRole("button", { name: "Cancel" })
      .click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();

    await page.mouse.click(4, 4);
    await expect(dialog).toBeVisible();
  });
});

test.describe("Artifact regeneration is reachable", () => {
  test("an expired Batch Artifact is offered from the workspace", async ({
    page,
  }) => {
    await page.goto("./admin/fulfillment");

    const expired = page.getByRole("region", {
      name: "Expired Batch Artifacts",
    });
    await expired.locator("summary").click();
    await expect(expired.getByText("Harbor Point")).toBeVisible();

    await expired.getByRole("button", { name: "Regenerate artifact" }).click();
    await expect(page.getByRole("dialog")).toHaveAccessibleDescription(
      /checksum are unchanged/,
    );
  });

  test("regeneration posts to the artifact endpoint, not a transition", async ({
    page,
  }) => {
    await page.goto(`./admin/fulfillment/requests/${EXPIRED_ARTIFACT_ID}`);

    await page
      .getByRole("region", { name: "Actions" })
      .getByRole("button", { name: "Regenerate artifact" })
      .click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Reason (required)").fill("Customer asked again.");
    await dialog.getByRole("button", { name: "Regenerate artifact" }).click();

    await expect
      .poll(() => calls.map((call) => call.url))
      .toContain(`/api/admin/requests/${EXPIRED_ARTIFACT_ID}/artifact/regenerate`);
  });
});

test.describe("Stale state", () => {
  test("a refused action reports why and re-reads the record", async ({
    page,
  }) => {
    await page.goto(`./admin/fulfillment/requests/${PENDING_REQUEST_ID}`);

    /*
     * Wait for the *pending* record to be on screen before overriding the
     * endpoint. Registering the stale response first lets it answer the initial
     * load too: the page then renders an already-approved request, the Approve
     * button never exists, and the click below times out after 30s. That is a
     * race against how quickly the first fetch leaves the page, which is why it
     * only failed on some machines and some worker counts.
     */
    const actionsRegion = page.getByRole("region", { name: "Actions" });
    await expect(actionsRegion.getByRole("button", { name: "Approve" })).toBeVisible();

    // The record moved on elsewhere — in Telegram, or in another tab.
    let reloaded = false;
    await page.route(
      `**/api/admin/requests/${PENDING_REQUEST_ID}`,
      async (route) => {
        reloaded = true;
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            ...STALE_APPROVED_DETAIL,
          }),
        });
      },
    );
    await page.route(
      `**/api/admin/requests/${PENDING_REQUEST_ID}/approve`,
      (route) =>
        route.fulfill({
          status: 409,
          contentType: "application/json",
          body: JSON.stringify({
            detail: "Action approve is not valid while request is approved.",
          }),
        }),
    );

    await actionsRegion.getByRole("button", { name: "Approve" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Reason (required)").fill("Stale view.");
    await dialog.getByRole("button", { name: "Approve" }).click();

    await expect(
      dialog.getByText("Action approve is not valid while request is approved."),
    ).toBeVisible();
    // The screen must not keep offering the action the domain just refused.
    await expect.poll(() => reloaded).toBe(true);
  });
});

test.describe("Telegram-originated state", () => {
  test("names the live Telegram decision without offering it twice", async ({
    page,
  }) => {
    await page.goto(`./admin/fulfillment/requests/${PENDING_REQUEST_ID}`);

    await expect(
      page.getByText(
        "This decision is also open in Telegram. Acting here settles it in both places.",
      ),
    ).toBeVisible();
    await expect(
      page.getByRole("region", { name: "Actions" }).getByRole("button", {
        name: "Approve",
      }),
    ).toHaveCount(1);
  });

  test("attributes a decision taken in Telegram", async ({ page }) => {
    await page.goto(`./admin/fulfillment/requests/${PENDING_REQUEST_ID}`);

    const activity = page.getByRole("region", { name: "Activity" });
    await activity.locator("summary").first().click();
    await expect(activity.getByText(/telegram:12345/)).toBeVisible();
    await expect(activity.getByText("pending")).toBeVisible();
    await expect(activity.getByText("approved")).toBeVisible();
  });
});

test.describe("Inventory Conflicts", () => {
  test("explains the competing requests, overlap, and recurrence rule", async ({
    page,
  }) => {
    await page.goto(`./admin/fulfillment/conflicts/${CONFLICT_ID}`);

    await expect(
      page.getByRole("heading", { level: 3, name: "Older Batch Request" }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { level: 3, name: "Newer Batch Request" }),
    ).toBeVisible();
    await expect(
      page.getByText("1 Leads eligible for both requests"),
    ).toBeVisible();
    await expect(
      page.getByText(/may recur only after a material change/),
    ).toBeVisible();
  });

  test("a decision states that it authorizes exactly one attempt", async ({
    page,
  }) => {
    await page.goto(`./admin/fulfillment/conflicts/${CONFLICT_ID}`);

    await page
      .getByRole("region", { name: "Decision" })
      .getByRole("button", { name: "Confirm once" })
      .click();

    await expect(page.getByRole("dialog")).toHaveAccessibleDescription(
      /Authorizes one allocation attempt for this exact inventory snapshot/,
    );
  });
});

test.describe("Mobile operation", () => {
  test("the workspace is complete and operable at the current viewport", async ({
    page,
  }) => {
    await page.goto("./admin/fulfillment");

    // All three areas of work remain reachable on small screens.
    for (const section of [
      "Batch Requests",
      "Inventory Conflicts",
      "Delivery failures",
    ]) {
      await expect(page.getByRole("region", { name: section })).toBeVisible();
    }

    await page
      .getByRole("region", { name: "Batch Requests" })
      .locator("summary")
      .click();
    const approve = page
      .getByRole("region", { name: "Batch Requests" })
      .getByRole("button", { name: "Approve" })
      .first();
    await expect(approve).toBeVisible();
    const box = await approve.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.height).toBeGreaterThanOrEqual(44);

    await approve.click();
    await expect(page.getByRole("dialog")).toBeVisible();
  });

  test("a delivery failure shows the exact provider error", async ({ page }) => {
    await page.goto("./admin/fulfillment");

    await page
      .getByRole("region", { name: "Delivery failures" })
      .locator("summary")
      .click();
    await expect(
      page
        .getByRole("region", { name: "Delivery failures" })
        .getByText("Provider returned HTTP 503"),
    ).toBeVisible();
  });
});
