import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

import { mockCustomerAuth } from "./customer-auth-fixtures";
import { NORTHSTAR_WALLET } from "./customer-home-fixtures";
import {
  BATCHES_DAY,
  DELIVERED_REQUEST,
  EMPTY_BATCH_REQUEST_WORKSPACE,
  EXPIRED_REQUEST,
  REPORTED_PHONE,
  WAITING_REQUEST,
  historyPage,
  mockBatchRequests,
} from "./customer-requests-fixtures";

type Options = Parameters<typeof mockBatchRequests>[1];

async function open(page: Page, path: string, options: Options = {}) {
  await page.clock.install({ time: BATCHES_DAY });
  await mockCustomerAuth(page, { billing: { wallet: NORTHSTAR_WALLET } });
  const state = await mockBatchRequests(page, options);
  await page.goto(path);
  return state;
}

function rowFor(page: Page, label: string) {
  return page
    .getByRole("table", { name: "Batches" })
    .getByRole("row")
    .filter({ has: page.getByRole("link", { name: `Batch ${label}`, exact: true }) });
}

test.describe("Batches", () => {
  test("lists every batch with its status and one action per row", async ({ page }) => {
    await open(page, "./batches");

    await expect(page.getByRole("heading", { level: 1, name: "Batches" })).toBeVisible();
    await expect(page.getByText("Every batch you have requested. Files stay available for 30 days.")).toBeVisible();
    await expect(page.locator("main").getByRole("link", { name: "New request" }))
      .toHaveAttribute("href", "/app/new-request");

    const table = page.getByRole("table", { name: "Batches" });
    await expect(table.getByRole("columnheader")).toHaveText([
      "Batch", "Leads", "States", "Status", "Submitted", "Delivered", "Cost", "Action",
    ]);
    await expect(table.getByRole("row")).toHaveCount(9);

    const waiting = rowFor(page, "#1042");
    await expect(waiting).toContainText("25,000");
    await expect(waiting).toContainText("Waiting for approval");
    await expect(waiting).toContainText("$125.00 held");
    await expect(waiting.getByRole("button")).toHaveText("Cancel");

    await expect(rowFor(page, "#1038").getByRole("link", { name: "Download Batch #1038" }))
      .toHaveAttribute("href", `/api/me/batch-requests/${DELIVERED_REQUEST.id}/artifact`);
    await expect(rowFor(page, "#1024")).toContainText("Expires Nov 12");
    await expect(rowFor(page, "#1017")).toContainText("File expired");
    await expect(rowFor(page, "#1017").getByRole("button", { name: "Get a new copy of Batch #1017" }))
      .toBeVisible();
    const rejected = rowFor(page, "#1012");
    await expect(rejected).toContainText("Not approved");
    await expect(rejected).toContainText("$0.00");
    await expect(rejected.getByRole("button")).toHaveCount(0);
    await expect(rejected.getByRole("link")).toHaveCount(1);

    // Supply stays hidden and internal words never reach the Customer (V6).
    await expect(page.locator("main")).not.toContainText(/REQ-|Batch Artifact|Batch Hold|Licensed States|inventory/);
    await expect(
      page.getByRole("navigation", { name: "Customer" }).getByRole("link", { name: "Batches" }),
    ).toHaveAttribute("aria-current", "page");
  });

  test("pages through every batch, newest first", async ({ page }) => {
    const state = await open(page, "./batches", { pages: historyPage });

    await expect(page.getByText("Showing 1–8 of 24")).toBeVisible();
    const pages = page.getByRole("navigation", { name: "Batch pages" });
    await expect(pages.getByRole("link", { name: "Previous" })).toHaveCount(0);
    await pages.getByRole("link", { name: "Next" }).click();
    await expect(page).toHaveURL(/\/app\/batches\?page=2$/);
    await expect(page.getByText("Showing 9–16 of 24")).toBeVisible();
    await pages.getByRole("link", { name: "Next" }).click();
    await expect(page.getByText("Showing 17–24 of 24")).toBeVisible();
    await expect(pages.getByRole("link", { name: "Next" })).toHaveCount(0);
    await pages.getByRole("link", { name: "Previous" }).click();
    await expect(page.getByText("Showing 9–16 of 24")).toBeVisible();
    expect(state.workspacePages).toEqual(expect.arrayContaining([1, 2, 3]));
  });

  test("downloads a ready file from its row", async ({ page }) => {
    const state = await open(page, "./batches");

    const download = page.waitForEvent("download");
    await rowFor(page, "#1038").getByRole("link", { name: "Download Batch #1038" }).click();
    expect((await download).suggestedFilename()).toBe("northstar_batch_1038.zip");
    expect(state.artifactDownloads).toEqual([DELIVERED_REQUEST.id]);
  });

  test("cancels a batch waiting for approval after confirming", async ({ page }) => {
    const state = await open(page, "./batches");

    await rowFor(page, "#1042").getByRole("button", { name: "Cancel Batch #1042" }).click();
    const dialog = page.getByRole("dialog", { name: "Cancel Batch #1042?" });
    await expect(dialog).toHaveAccessibleDescription(/releases the \$125\.00 held/);
    await dialog.getByRole("button", { name: "Cancel batch" }).click();

    await expect(page.getByRole("status").filter({ hasText: "Batch #1042 was canceled." })).toBeVisible();
    await expect(rowFor(page, "#1042")).toContainText("Canceled");
    await expect(rowFor(page, "#1042").getByRole("button")).toHaveCount(0);
    expect(state.cancellations).toEqual([WAITING_REQUEST.id]);
  });

  test("asks for a new copy of an expired file from its row", async ({ page }) => {
    const state = await open(page, "./batches");

    await rowFor(page, "#1017").getByRole("button", { name: "Get a new copy of Batch #1017" }).click();

    await expect(page.getByRole("status").filter({ hasText: "A new copy of Batch #1017 is on the way." }))
      .toBeVisible();
    await expect(rowFor(page, "#1017")).toContainText("New copy on the way");
    await expect(rowFor(page, "#1017").getByRole("button")).toHaveCount(0);
    expect(state.copies).toEqual([EXPIRED_REQUEST.id]);
  });

  test("says why a new copy was refused", async ({ page }) => {
    await open(page, "./batches", {
      refuseCopy: { status: 429, detail: "You have asked for 5 new copies in the last 24 hours. Try again tomorrow, or email noah@jawnix.com." },
    });

    await rowFor(page, "#1017").getByRole("button", { name: "Get a new copy of Batch #1017" }).click();

    await expect(page.getByRole("alert")).toContainText("Try again tomorrow");
    await expect(rowFor(page, "#1017")).toContainText("File expired");
  });

  test("points a new Customer at their first request", async ({ page }) => {
    await open(page, "./batches", { workspace: EMPTY_BATCH_REQUEST_WORKSPACE });

    await expect(page.getByRole("heading", { level: 2, name: "No batches yet" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Request your first batch" }))
      .toHaveAttribute("href", "/app/new-request");
    await expect(page.getByRole("table")).toHaveCount(0);
  });
});

test.describe("The batch page", () => {
  test("an expired batch shows its leads, files, charge, and history", async ({ page }) => {
    await open(page, "./batches/1017");

    await expect(page.getByRole("heading", { level: 1, name: "Batch #1017" })).toBeVisible();
    await expect(page.getByText("50,000 leads · FL, TX · delivered Oct 2")).toBeVisible();
    await expect(page.locator(".jx-page__actions")).toContainText("File expired Nov 1");

    const byState = page.getByRole("table", { name: "Leads by state" });
    await expect(byState.getByRole("row")).toHaveText([
      /State\s*Leads/,
      /Florida\s*31,240/,
      /Texas\s*18,760/,
      /Total\s*50,000/,
    ]);

    const files = page.getByRole("region", { name: "Files" });
    await expect(files).toContainText("5 CSV files of 10,000 rows, in one zip");
    await expect(files).toContainText("phone, title, state");
    await expect(files).toContainText("Nov 1 (expired)");
    await expect(files).toContainText("Same leads, ready in a few minutes, emailed to you");

    const charge = page.getByRole("region", { name: "Charge" });
    await expect(charge).toContainText("Rate$5.00 per 1,000");
    await expect(charge).toContainText("Charged Oct 2$250.00");
    await expect(charge).toContainText(/Request submittedOct 1 14:20/);
    await expect(charge).toContainText(/DeliveredOct 2 08:05/);

    const history = page.getByRole("list", { name: "History of Batch #1017" });
    await expect(history.getByRole("listitem")).toHaveText([
      /Submitted.*Oct 1/,
      /Approved.*Oct 1/,
      /Delivered.*Oct 2/,
      /File expired.*Nov 1/,
    ]);

    await expect(
      page.getByRole("navigation", { name: "Customer" }).getByRole("link", { name: "Batches" }),
    ).toHaveAttribute("aria-current", "page");
  });

  test("gets a new copy of an expired file", async ({ page }) => {
    const state = await open(page, "./batches/1017");

    await page.locator(".jx-page__actions").getByRole("button", { name: "Get a new copy of Batch #1017" }).click();

    await expect(page.getByRole("status").filter({ hasText: "A new copy of Batch #1017 is on the way." }))
      .toBeVisible();
    await expect(page.locator(".jx-page__actions")).toContainText("New copy on the way");
    await expect(page.getByRole("region", { name: "Files" })).toContainText("On the way. We email you when it is ready.");
    await expect(page.getByRole("list", { name: "History of Batch #1017" })).toContainText("New copy on the way");
    expect(state.copies).toEqual([EXPIRED_REQUEST.id]);
  });

  test("reports a bad lead through the existing report", async ({ page }) => {
    const state = await open(page, "./batches/1017");
    const form = page.getByRole("region", { name: "Report a bad lead" });

    await form.getByRole("textbox", { name: "Phone number" }).fill("(555) 555-0199");
    await form.getByRole("button", { name: "Send report" }).click();
    await expect(form.getByText("That number is not in Batch #1017. Check the number and try again.")).toBeVisible();
    expect(state.reports).toHaveLength(0);

    await form.getByRole("textbox", { name: "Phone number" }).fill(REPORTED_PHONE);
    await form.getByRole("combobox", { name: "What is wrong?" }).selectOption({ label: "Wrong state" });
    await form.getByRole("textbox", { name: "Details (optional)" }).fill("Listed in Florida, based in Georgia.");
    await form.getByRole("button", { name: "Send report" }).click();

    await expect(form.getByRole("status")).toHaveText(
      "Report sent for Sunshine Roofing. Noah reviews every report and replies by email.",
    );
    expect(state.lookups.at(-1)).toEqual({ phone: REPORTED_PHONE, request_id: EXPIRED_REQUEST.id });
    expect(state.reports).toEqual([
      { eventId: "77", body: { reason: "wrong_state", details: "Listed in Florida, based in Georgia." } },
    ]);
    await expect(form.getByRole("textbox", { name: "Phone number" })).toHaveValue("");
  });

  test("a batch waiting for approval shows what is held and can be canceled", async ({ page }) => {
    const state = await open(page, "./batches/1042");

    await expect(page.getByRole("heading", { level: 1, name: "Batch #1042" })).toBeVisible();
    await expect(page.locator(".jx-page__actions")).toContainText("Waiting for approval");
    const charge = page.getByRole("region", { name: "Charge" });
    await expect(charge).toContainText("Held until delivery$125.00");
    await expect(page.getByRole("region", { name: "Files" })).toContainText("3 CSV files of up to 10,000 rows, in one zip");
    await expect(page.getByRole("region", { name: "Files" })).toContainText("30 days after delivery");
    await expect(page.getByRole("region", { name: "Report a bad lead" })).toHaveCount(0);
    await expect(page.getByRole("table", { name: "Leads by state" })).toHaveCount(0);

    await page.locator(".jx-page__actions").getByRole("button", { name: "Cancel Batch #1042" }).click();
    await page.getByRole("dialog", { name: "Cancel Batch #1042?" }).getByRole("button", { name: "Cancel batch" }).click();
    await expect(page.locator(".jx-page__actions")).toContainText("Canceled");
    expect(state.cancellations).toEqual([WAITING_REQUEST.id]);
  });

  test("a ready batch downloads from its header", async ({ page }) => {
    const state = await open(page, "./batches/1038");

    const download = page.waitForEvent("download");
    await page.locator(".jx-page__actions").getByRole("link", { name: "Download Batch #1038" }).click();
    await download;
    expect(state.artifactDownloads).toEqual([DELIVERED_REQUEST.id]);
  });

  test("a UUID link settles on the batch number", async ({ page }) => {
    await open(page, `./batches/${EXPIRED_REQUEST.id}`);

    await expect(page.getByRole("heading", { level: 1, name: "Batch #1017" })).toBeVisible();
    await expect(page).toHaveURL(/\/app\/batches\/1017$/);
  });

  test("an unknown batch is a safe not-found page", async ({ page }) => {
    await open(page, "./batches/9999");

    await expect(page.getByRole("heading", { level: 1, name: "Batch not found" })).toBeVisible();
    await expect(page.getByRole("link", { name: "All batches" })).toHaveAttribute("href", "/app/batches");
  });
});

test.describe("Old request and Feedback links", () => {
  test("an emailed request link opens its batch page", async ({ page }) => {
    await open(page, `./requests?request=${EXPIRED_REQUEST.id}`);

    await expect(page.getByRole("heading", { level: 1, name: "Batch #1017" })).toBeVisible();
    await expect(page).toHaveURL(/\/app\/batches\/1017$/);
  });

  test("a page of the old history opens that page of Batches", async ({ page }) => {
    await open(page, "./requests?page=2", { pages: historyPage });

    await expect(page).toHaveURL(/\/app\/batches\?page=2$/);
    await expect(page.getByText("Showing 9–16 of 24")).toBeVisible();
  });

  test("Feedback opens the batch it was scoped to, or Batches", async ({ page }) => {
    await open(page, `./feedback?request=${EXPIRED_REQUEST.id}`);
    await expect(page.getByRole("heading", { level: 1, name: "Batch #1017" })).toBeVisible();

    await page.goto("./feedback");
    await expect(page.getByRole("heading", { level: 1, name: "Batches" })).toBeVisible();
    await expect(page).toHaveURL(/\/app\/batches$/);
  });

  test("a signed-out batch link survives sign-in", async ({ page }) => {
    const auth = await mockCustomerAuth(page, { overviewStatus: 401 });
    await mockBatchRequests(page);
    await page.goto("./batches/1017");

    await expect(page.getByRole("heading", { level: 1, name: "Sign in" })).toBeVisible();
    expect(new URL(page.url()).searchParams.get("next")).toBe("/app/batches/1017");

    await page.getByLabel("Email address (required)").fill("customer@example.com");
    await page.getByLabel("Password (required)").fill("customer-known-password-48");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect.poll(() => auth.sessionRequests.length).toBe(1);
    expect(auth.sessionRequests[0]).toMatchObject({ requested_next: "/app/batches/1017" });
  });
});
