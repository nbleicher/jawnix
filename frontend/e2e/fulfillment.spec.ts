import { expect, test } from "@playwright/test";

import {
  CONFLICT_ID,
  DELIVERED_REQUEST_ID,
  EXPIRED_ARTIFACT_ID,
  FAILED_WITHOUT_ARTIFACT_ID,
  FAILED_WITH_ARTIFACT_ID,
  PENDING_REQUEST_ID,
  asBatch,
  mockFulfillment,
} from "./fulfillment-fixtures";
import { mockLeadReports } from "./lead-report-fixtures";
import { mockAdminMFA } from "./mfa-fixtures";
import type { RecordedCall } from "./fulfillment-fixtures";

/**
 * Core Fulfillment operations in a real browser (#57).
 *
 * The Fulfillment workspace page is gone (A4): its queues are covered on
 * Today and All batches, and its suppressed Leads and pool analytics on
 * Inventory (inventory.spec.ts). What remains here are the Batch page
 * actions, reached through the old request links, and the Inventory
 * Conflict page.
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

test.describe("The Batch page replaced the Fulfillment request detail", () => {
  test("old request links land on the Batch page, named by number", async ({ page }) => {
    await page.goto(`./admin/fulfillment/requests/${PENDING_REQUEST_ID}`);
    await expect(page).toHaveURL(/\/app\/admin\/batches\/1042$/);
    await expect(
      page.getByRole("heading", { level: 1, name: "Batch #1042 · Northstar Insurance" }),
    ).toBeVisible();
  });
});

test.describe("Only domain-valid actions are offered", () => {
  test("a pending Batch Request offers approve, reject, and cancel", async ({
    page,
  }) => {
    await page.goto(`./admin/fulfillment/requests/${PENDING_REQUEST_ID}`);

    const actions = page.getByRole("main");
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

    const actions = page.getByRole("main");
    await expect(page.getByRole("heading", { level: 1, name: "Batch #1043 · Harbor Point" })).toBeVisible();
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

    const actions = page.getByRole("main");
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

    const actions = page.getByRole("main");
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
      .getByRole("main")
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
      .getByRole("main")
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
      .getByRole("main")
      .getByRole("button", { name: "Cancel" })
      .click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();

    await page.mouse.click(4, 4);
    await expect(dialog).toBeVisible();
  });
});

test.describe("Telegram re-notification is reachable (B5)", () => {
  test("an open Batch Request re-notifies from its own page", async ({
    page,
  }) => {
    await page.goto(`./admin/fulfillment/requests/${PENDING_REQUEST_ID}`);

    await page
      .getByRole("main")
      .getByRole("button", { name: "Re-notify in Telegram" })
      .click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("button", { name: "Re-notify" }).click();
    await expect(dialog.getByText("A reason is required.")).toBeVisible();
    expect(calls).toHaveLength(0);

    await dialog.getByLabel("Reason (required)").fill("Message was deleted.");
    await dialog.getByRole("button", { name: "Re-notify" }).click();

    await expect
      .poll(() => calls.map((call) => call.url))
      .toContain(`/api/admin/requests/${PENDING_REQUEST_ID}/notify`);
    expect(calls.at(-1)?.reason).toBe("Message was deleted.");
  });

  test("a delivered Batch Request does not offer it", async ({ page }) => {
    await page.goto(`./admin/fulfillment/requests/${DELIVERED_REQUEST_ID}`);

    await expect(
      page.getByRole("heading", { level: 1, name: /Harbor Point/ }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Re-notify in Telegram" }),
    ).toHaveCount(0);
  });
});

test.describe("Artifact regeneration is reachable", () => {
  test("regeneration posts to the artifact endpoint, not a transition", async ({
    page,
  }) => {
    await page.goto(`./admin/fulfillment/requests/${EXPIRED_ARTIFACT_ID}`);

    await page
      .getByRole("main")
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
    // The number address directly: the UUID redirect re-reads the record once
    // more, which would race the stale override below.
    await page.goto("./admin/batches/1042");

    /*
     * Wait for the *pending* record to be on screen before overriding the
     * endpoint. Registering the stale response first lets it answer the initial
     * load too: the page then renders an already-approved request, the Approve
     * button never exists, and the click below times out after 30s. That is a
     * race against how quickly the first fetch leaves the page, which is why it
     * only failed on some machines and some worker counts.
     */
    const actionsRegion = page.getByRole("main");
    await expect(actionsRegion.getByRole("button", { name: "Approve" })).toBeVisible();

    // The record moved on elsewhere — in Telegram, or in another tab.
    let reloaded = false;
    await page.route(
      "**/api/admin/batches/1042",
      async (route) => {
        reloaded = true;
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(asBatch({ ...STALE_APPROVED_DETAIL, number: 1042 })),
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
      page.getByText("Also open in Telegram. Acting here settles it in both places."),
    ).toBeVisible();
    await expect(
      page.getByRole("main").getByRole("button", {
        name: "Approve",
      }),
    ).toHaveCount(1);
  });

  test("attributes a decision taken in Telegram", async ({ page }) => {
    await page.goto(`./admin/fulfillment/requests/${PENDING_REQUEST_ID}`);

    const activity = page.getByRole("table", { name: "Activity" });
    await expect(activity.getByText("Operator in Telegram")).toBeVisible();
    await expect(activity.getByText("Approved · Telegram Batch Request decision")).toBeVisible();
  });
});

test.describe("Inventory Conflicts", () => {
  test("puts the decision first and links both batches", async ({ page }) => {
    await page.goto(`./admin/fulfillment/conflicts/${CONFLICT_ID}`);

    await expect(
      page.getByText("Batch #1036 cannot be filled yet, and Batch #1037 could use leads both need."),
    ).toBeVisible();
    const decision = page.getByRole("region", { name: "The decision" });
    await expect(decision.getByText("Leads both batches need")).toBeVisible();
    const batches = page.getByRole("table", { name: "The two batches" });
    await expect(batches.getByRole("link", { name: "Batch #1036" })).toHaveAttribute(
      "href",
      "/app/admin/batches/1036",
    );
    await expect(batches.getByRole("link", { name: "Batch #1037" })).toHaveAttribute(
      "href",
      "/app/admin/batches/1037",
    );
    await expect(page.getByText(/may recur only after a material change/)).toBeVisible();
  });

  test("a decision states that it authorizes exactly one attempt", async ({
    page,
  }) => {
    await page.goto(`./admin/fulfillment/conflicts/${CONFLICT_ID}`);

    await page.getByRole("main").getByRole("button", { name: "Confirm once" }).click();

    await expect(page.getByRole("dialog")).toHaveAccessibleDescription(
      /Authorizes one allocation attempt for this exact inventory snapshot/,
    );
  });
});
