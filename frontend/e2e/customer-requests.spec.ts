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

function statusLine(page: Page, name: RegExp): Locator {
  return page.getByRole("status", { name });
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

test.describe("The milestone status line", () => {
  test("reads as one live line that names the current state", async ({
    page,
  }) => {
    await openRequestDetail(page);

    const line = statusLine(page, /Progress for the 750 lead request/);
    await expect(line).toHaveCount(1);
    await expect(line).toHaveAttribute("aria-live", "polite");
    await expect(line).toContainText("Waiting for Inventory");
    await expect(line).toContainText("Nothing has gone wrong");

    // A paused request holds a hollow ring — nothing spins.
    await expect(line.locator(".jx-statusline__spinner")).toHaveCount(0);
    await expect(line.locator(".jx-statusline__ring--paused")).toHaveCount(1);
  });

  test("is a single inline statement, not rows", async ({ page }) => {
    await openRequestDetail(page);

    const line = statusLine(page, /Progress for the 750 lead request/);
    await expect(line).toHaveCount(1);
    await expect(line.getByRole("listitem")).toHaveCount(0);

    // The witness sits inline with the statement on the same first row.
    const lineBox = await line.boundingBox();
    const ringBox = await line
      .locator(".jx-statusline__ring--paused")
      .boundingBox();
    expect(lineBox).not.toBeNull();
    expect(ringBox).not.toBeNull();
    expect(Math.abs(ringBox!.y - lineBox!.y)).toBeLessThan(8);
  });

  test("spins while a request is in flight", async ({ page }) => {
    const state = await openRequests(page, {
      workspace: EMPTY_BATCH_REQUEST_WORKSPACE,
    });
    await completeGuidedFlow(page);
    await expect(
      page.getByRole("heading", { name: "Request submitted" }),
    ).toBeVisible();
    expect(state.submissions).toHaveLength(1);

    const line = statusLine(page, /Progress for the request you just submitted/);
    await expect(line).toContainText("Submitted");
    const spinner = line.locator(".jx-statusline__spinner");
    await expect(spinner).toHaveCount(1);
    await expect(spinner).toHaveCSS("animation-name", "jx-statusline-spin");
  });

  test("stamps a terminal outcome instead of spinning", async ({ page }) => {
    await openRequestDetail(page, REJECTED_REQUEST.id);

    const line = statusLine(page, /Progress for the 300 lead request/);
    await expect(line).toContainText("Not Approved");
    await expect(line).toContainText("This request was not approved");
    await expect(line.locator(".jx-statusline__spinner")).toHaveCount(0);
    await expect(line.locator(".jx-statusline__witness--danger")).toHaveCount(1);
  });

  test("stamps a delivered request with its timestamp", async ({ page }) => {
    await openRequestDetail(page, DELIVERED_REQUEST.id, {
      workspace: {
        ...BATCH_REQUEST_WORKSPACE,
        requests: [DELIVERED_REQUEST],
      },
    });

    const line = statusLine(page, /Progress for the 750 lead request/);
    await expect(line).toContainText("Delivered");
    await expect(line).toContainText("· 2026-07-27 16:00 UTC");
    await expect(line.locator(".jx-statusline__witness--ink")).toHaveCount(1);
  });
});

test.describe("The status line with motion suppressed", () => {
  test("says exactly the same thing and does not spin", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await openRequests(page, { workspace: EMPTY_BATCH_REQUEST_WORKSPACE });
    await completeGuidedFlow(page);
    await expect(
      page.getByRole("heading", { name: "Request submitted" }),
    ).toBeVisible();

    const line = statusLine(page, /Progress for the request you just submitted/);
    await expect(line).toContainText("Submitted");
    const spinner = line.locator(".jx-statusline__spinner");
    await expect(spinner).toHaveCount(1);
    await expect(spinner).not.toHaveCSS("animation-name", "jx-statusline-spin");
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
    const line = statusLine(page, /Progress for the 750 lead request/);
    await expect(line).toContainText("Canceled");
    await expect(line).toContainText("This request was withdrawn");
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
      page.getByRole("status", { name: /Progress for the 750 lead request/ }),
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
    await expect(card.getByText("Expired", { exact: true })).toBeVisible();
    await expect(
      card.getByText(/Email noah@jawnix\.com and I'll regenerate/),
    ).toBeVisible();
    await expect(card.getByText(/retained for 30 days/)).toBeVisible();
    await expect(card.getByRole("link", { name: /Download/ })).toHaveCount(0);
  });

  test("matches the visual baseline", async ({ page }) => {
    await openRequestDetail(page);
    await expect(page.locator("html")).toHaveAttribute("data-scheme", "light");

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
