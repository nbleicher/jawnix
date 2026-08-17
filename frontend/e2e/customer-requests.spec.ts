import { expect, test } from "@playwright/test";
import type { Locator, Page } from "@playwright/test";

import { mockCustomerAuth } from "./customer-auth-fixtures";
import {
  BATCH_REQUEST_WORKSPACE,
  BLOCKED_BATCH_REQUEST_WORKSPACE,
  DELIVERED_REQUEST,
  EMPTY_BATCH_REQUEST_WORKSPACE,
  REJECTED_REQUEST,
  WAITING_REQUEST,
  mockBatchRequests,
} from "./customer-requests-fixtures";

async function openRequests(page: Page, options: Parameters<typeof mockBatchRequests>[1] = {}) {
  await mockCustomerAuth(page);
  const state = await mockBatchRequests(page, options);
  await page.goto("./requests");
  await expect(page.getByRole("heading", { level: 1, name: "Requests" })).toBeVisible();
  return state;
}

async function openRequestDetail(
  page: Page,
  requestId: string = WAITING_REQUEST.id,
  options: Parameters<typeof mockBatchRequests>[1] = {},
) {
  await mockCustomerAuth(page);
  const state = await mockBatchRequests(page, options);
  await page.goto(`./requests?request=${requestId}`);
  await expect(
    page.getByRole("heading", { level: 1, name: "Batch Request" }),
  ).toBeVisible();
  return state;
}

/** Quantity → scope → one-file default → review, with nothing typed that is not needed. */
async function completeGuidedFlow(page: Page, quantity = "750") {
  await page.getByRole("spinbutton", { name: /How many leads/ }).fill(quantity);
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("radio", { name: "One file" })).toBeChecked();
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(
    page.getByRole("heading", { name: "Review your request" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Submit request" }).click();
}

function milestoneNodes(page: Page, name: RegExp): Locator {
  return page.getByRole("list", { name }).getByRole("listitem");
}

test.describe("Guided Batch Requests", () => {
  test("completes a valid request through four stages in well under a minute", async ({
    page,
  }) => {
    const state = await openRequests(page, {
      workspace: EMPTY_BATCH_REQUEST_WORKSPACE,
    });

    const started = Date.now();
    await completeGuidedFlow(page);
    await expect(
      page.getByRole("heading", { name: "Request submitted" }),
    ).toBeVisible();
    const elapsed = Date.now() - started;

    expect(elapsed).toBeLessThan(60_000);
    expect(state.submissions).toHaveLength(1);
    expect(state.submissions[0]).toMatchObject({
      lead_count: 750,
      state_mode: "all_saved",
    });
    expect(state.submissions[0]).not.toHaveProperty("rows_per_file");
    await expect(
      page.getByRole("link", { name: "View this Batch Request" }),
    ).toHaveAttribute(
      "href",
      "/app/requests?request=33333333-3333-4333-8333-333333333333",
    );
  });

  test("splits by rows per file with a live preview and freezes the choice", async ({
    page,
  }) => {
    const state = await openRequests(page, {
      workspace: EMPTY_BATCH_REQUEST_WORKSPACE,
    });

    await page.getByRole("spinbutton", { name: /How many leads/ }).fill("50000");
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByRole("radio", { name: /Split by rows per file/ }).check();
    await page.getByRole("spinbutton", { name: /Rows per file/ }).fill("10000");
    await expect(
      page.getByText("50,000 at 10,000/file = 5 files"),
    ).toBeVisible();
    await page.getByRole("button", { name: "Continue" }).click();
    await expect(page.getByText("50,000 at 10,000/file = 5 files")).toBeVisible();
    await page.getByRole("button", { name: "Submit request" }).click();

    await expect(
      page.getByRole("heading", { name: "Request submitted" }),
    ).toBeVisible();
    expect(state.submissions).toHaveLength(1);
    expect(state.submissions[0]).toMatchObject({
      lead_count: 50_000,
      rows_per_file: 10_000,
      state_mode: "all_saved",
    });
  });

  test("keeps invalid and unlicensed requests out of the review stage", async ({
    page,
  }) => {
    const state = await openRequests(page, {
      workspace: EMPTY_BATCH_REQUEST_WORKSPACE,
    });

    await page.getByRole("spinbutton", { name: /How many leads/ }).fill("100001");
    await page.getByRole("button", { name: "Continue" }).click();
    await expect(page.getByRole("alert")).toContainText(
      "Enter between 1 and 100,000 leads.",
    );
    await expect(
      page.getByRole("heading", { name: "Review your request" }),
    ).toHaveCount(0);

    await page.getByRole("spinbutton", { name: /How many leads/ }).fill("750");
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByRole("radio", { name: /Choose specific states/ }).check();
    await page.getByRole("button", { name: "Continue" }).click();
    await expect(page.getByRole("alert")).toContainText(
      "Choose at least one Licensed State.",
    );

    // Only Licensed States are offered, so an unlicensed scope is unreachable.
    await expect(page.getByRole("checkbox")).toHaveCount(2);
    await expect(page.getByRole("checkbox", { name: "FL" })).toBeVisible();
    await expect(page.getByRole("checkbox", { name: "TX" })).toBeVisible();
    await expect(page.getByRole("checkbox", { name: "NY" })).toHaveCount(0);
    expect(state.submissions).toHaveLength(0);
  });

  test("a double-clicked submit creates exactly one Batch Request", async ({
    page,
  }) => {
    const state = await openRequests(page, {
      workspace: EMPTY_BATCH_REQUEST_WORKSPACE,
      submitDelayMs: 600,
    });

    await page.getByRole("spinbutton", { name: /How many leads/ }).fill("750");
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByRole("button", { name: "Submit request" }).dblclick();

    await expect(
      page.getByRole("heading", { name: "Request submitted" }),
    ).toBeVisible();
    const keys = new Set(
      state.submissions.map((body) => String(body.idempotency_key)),
    );
    expect(keys.size).toBe(1);
    await expect(page.getByText(/was not sent twice/)).toHaveCount(0);
  });

  test("offers the account fix instead of the stages when nothing valid can be entered", async ({
    page,
  }) => {
    await openRequests(page, { workspace: BLOCKED_BATCH_REQUEST_WORKSPACE });

    await expect(
      page.getByRole("heading", { level: 2, name: "Add a Licensed State first" }),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: "Add Licensed States" }),
    ).toHaveAttribute("href", "/app/account");
    await expect(
      page.getByRole("spinbutton", { name: /How many leads/ }),
    ).toHaveCount(0);
  });
});

test.describe("The milestone graph", () => {
  test("reads as an ordered, timestamped, state-labelled list", async ({
    page,
  }) => {
    await openRequestDetail(page);

    const nodes = milestoneNodes(page, /Progress for the 750 lead request/);
    await expect(nodes).toHaveCount(4);
    await expect(nodes.nth(0)).toContainText("Submitted");
    await expect(nodes.nth(0)).toContainText("Completed");
    await expect(nodes.nth(0)).toContainText("2026");
    await expect(nodes.nth(2)).toContainText("Paused");
    await expect(nodes.nth(2)).toHaveAttribute("aria-current", "step");
    await expect(nodes.nth(3)).toContainText("Not started");

    // The shape of the graph is also available as text.
    const summaryId = await page
      .getByRole("list", { name: /Progress for the 750 lead request/ })
      .getAttribute("aria-describedby");
    await expect(page.locator(`#${summaryId}`)).toHaveText(
      "At step 3 of 4, Preparing Batch, paused. Waiting for Inventory.",
    );
  });

  test("runs along the inline axis when there is room and stacks when there is not", async ({
    page,
  }, testInfo) => {
    await openRequestDetail(page);

    const nodes = milestoneNodes(page, /Progress for the 750 lead request/);
    const first = await nodes.nth(0).boundingBox();
    const second = await nodes.nth(1).boundingBox();
    expect(first).not.toBeNull();
    expect(second).not.toBeNull();

    if (testInfo.project.name === "mobile") {
      expect(second!.y).toBeGreaterThan(first!.y);
      expect(Math.abs(second!.x - first!.x)).toBeLessThan(2);
    } else {
      expect(second!.x).toBeGreaterThan(first!.x);
      expect(Math.abs(second!.y - first!.y)).toBeLessThan(2);
    }
  });

  test("never marks a milestone the request will not reach as merely upcoming", async ({
    page,
  }) => {
    await openRequestDetail(page, REJECTED_REQUEST.id);

    const nodes = milestoneNodes(page, /Progress for the 300 lead request/);
    await expect(nodes.nth(1)).toContainText("Stopped");
    await expect(nodes.nth(2)).toContainText("Not reached");
    await expect(nodes.nth(3)).toContainText("Not reached");
  });
});

test.describe("The milestone graph with motion suppressed", () => {
  test("says exactly the same thing", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await openRequestDetail(page);

    const nodes = milestoneNodes(page, /Progress for the 750 lead request/);
    await expect(nodes.nth(0)).toContainText("Completed");
    await expect(nodes.nth(2)).toContainText("Paused");
    await expect(nodes.nth(2)).toHaveAttribute("aria-current", "step");
    await expect(nodes.nth(3)).toContainText("Not started");
    await expect(page.getByText("Waiting for Inventory").first()).toBeVisible();
  });
});

test.describe("Outcomes and pauses", () => {
  test("explains Waiting for Inventory as a pause rather than a failure", async ({
    page,
  }) => {
    await openRequestDetail(page);

    await expect(page.getByText("Waiting for Inventory").first()).toBeVisible();
    await expect(
      page.getByText("Nothing has gone wrong", { exact: false }),
    ).toBeVisible();
    await expect(page.getByRole("alert")).toHaveCount(0);
    await expect(page.locator("body")).not.toContainText("waiting_inventory");
  });

  test("gives a rejected request its own outcome and a valid next action", async ({
    page,
  }) => {
    await openRequestDetail(page, REJECTED_REQUEST.id);

    await expect(
      page.getByText("This request was not approved.", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: "Request another Batch" }),
    ).toHaveAttribute("href", "/app/requests");
  });
});

test.describe("Pending cancellation", () => {
  test("appears only for the request the domain still allows withdrawing", async ({
    page,
  }) => {
    await openRequestDetail(page);

    await expect(page.getByRole("button", { name: "Cancel request" })).toHaveCount(1);
  });

  test("updates the timeline as soon as it is confirmed", async ({ page }) => {
    const state = await openRequestDetail(page);

    await page.getByRole("button", { name: "Cancel request" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText("It cannot be undone");
    await dialog.getByRole("button", { name: "Cancel request" }).click();

    await expect(
      page.getByText("This request was withdrawn", { exact: false }),
    ).toBeVisible();
    await expect(page.getByRole("button", { name: "Cancel request" })).toHaveCount(0);
    const nodes = milestoneNodes(page, /Progress for the 750 lead request/);
    await expect(nodes.nth(2)).toContainText("Stopped");
    expect(state.cancellations).toEqual([
      "11111111-1111-4111-8111-111111111111",
    ]);
  });
});

test.describe("Batch Request detail deep links", () => {
  test("a hard-loaded request link resolves to its own lifecycle", async ({
    page,
  }) => {
    await openRequestDetail(page);

    await expect(
      page.getByRole("heading", { name: "750 lead Batch Request" }),
    ).toBeVisible();
    await expect(
      page.getByRole("list", { name: /Progress for the 750 lead request/ }),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: "All Requests" }),
    ).toHaveAttribute("href", "/app/requests");
  });

  test("an unknown request link resolves to a safe not-found page", async ({
    page,
  }) => {
    await openRequestDetail(page, "42");

    await expect(
      page.getByRole("heading", { name: "Batch Request not found" }),
    ).toBeVisible();
  });

  test("a delivered request downloads its live artifact from the portal", async ({
    page,
  }) => {
    await page.clock.install({ time: new Date("2026-07-31T16:00:00Z") });
    const state = await openRequestDetail(page, DELIVERED_REQUEST.id, {
      workspace: {
        ...BATCH_REQUEST_WORKSPACE,
        requests: [DELIVERED_REQUEST],
      },
    });

    const card = page.getByRole("region", { name: "Batch Artifact" });
    await expect(card.getByText("requests-customer_batch.zip")).toBeVisible();
    await expect(card.getByText("750")).toBeVisible();
    await expect(
      card.getByText("requests-customer_batch_part_001.csv"),
    ).toBeVisible();
    await expect(card.getByText("300 rows").first()).toBeVisible();
    await expect(
      card.getByText("requests-customer_batch_part_003.csv"),
    ).toBeVisible();
    await expect(card.getByText("150 rows")).toBeVisible();
    await expect(card.getByText("Expires in 26 days")).toBeVisible();

    const downloadPromise = page.waitForEvent("download");
    await card.getByRole("link", { name: "Download zip" }).click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toBe("requests-customer_batch.zip");
    expect(state.artifactDownloads).toEqual([DELIVERED_REQUEST.id]);
  });

  test("an expired artifact says to contact Jawnix and cannot download", async ({
    page,
  }) => {
    await page.clock.install({ time: new Date("2026-08-27T16:00:00Z") });
    await openRequestDetail(page, DELIVERED_REQUEST.id, {
      workspace: {
        ...BATCH_REQUEST_WORKSPACE,
        requests: [
          {
            ...DELIVERED_REQUEST,
            artifact: {
              ...DELIVERED_REQUEST.artifact,
              available: false,
              download_href: null,
            },
          },
        ],
      },
    });

    const card = page.getByRole("region", { name: "Batch Artifact" });
    await expect(card.getByText("Expired — contact us")).toBeVisible();
    await expect(card.getByText(/retained for 30 days/)).toBeVisible();
    await expect(card.getByRole("link", { name: /Download/ })).toHaveCount(0);
  });

  test("matches the Match visual baseline", async ({ page }) => {
    await openRequestDetail(page);
    await expect(page.locator("html")).toHaveAttribute("data-theme", "match");

    await expect(page).toHaveScreenshot("customer-request-detail.png", {
      animations: "disabled",
      fullPage: true,
    });
  });
});

test.describe("Active request refresh", () => {
  test("starts for active work and stops once the last request settles", async ({
    page,
  }) => {
    await page.clock.install();
    const settledWorkspace = {
      ...BATCH_REQUEST_WORKSPACE,
      requests: [DELIVERED_REQUEST, REJECTED_REQUEST],
    };
    const state = await openRequests(page, {
      workspaceSequence: [BATCH_REQUEST_WORKSPACE, settledWorkspace],
    });

    expect(state.workspaceRequests).toBe(1);
    await page.clock.fastForward(10_000);
    await expect.poll(() => state.workspaceRequests).toBe(2);
    await expect(page.getByText("Your Batch is ready.")).toBeVisible();

    await page.clock.fastForward(30_000);
    expect(state.workspaceRequests).toBe(2);
  });

  test("never starts when all requests are already settled", async ({ page }) => {
    await page.clock.install();
    const state = await openRequests(page, {
      workspace: {
        ...BATCH_REQUEST_WORKSPACE,
        requests: [DELIVERED_REQUEST, REJECTED_REQUEST],
      },
    });

    await page.clock.fastForward(30_000);
    expect(state.workspaceRequests).toBe(1);
  });
});
