import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

import { mockCustomerAuth } from "./customer-auth-fixtures";
import { FREE_CUSTOMER_WALLET } from "./customer-billing-fixtures";
import { NORTHSTAR_WALLET } from "./customer-home-fixtures";
import {
  BATCH_REQUEST_WORKSPACE,
  BLOCKED_BATCH_REQUEST_WORKSPACE,
  EMPTY_BATCH_REQUEST_WORKSPACE,
  mockBatchRequests,
} from "./customer-requests-fixtures";

type Options = Parameters<typeof mockBatchRequests>[1];

async function openNewRequest(
  page: Page,
  options: Options = {},
  wallet: typeof NORTHSTAR_WALLET = NORTHSTAR_WALLET,
  path = "./new-request",
) {
  await mockCustomerAuth(page, {
    billing: {
      wallet,
      purchaseCheckoutUrl: "https://checkout.stripe.test/pay/cs_test_new_request",
    },
  });
  const state = await mockBatchRequests(page, options);
  await page.goto(path);
  await expect(page.getByRole("heading", { level: 1, name: "New request" })).toBeVisible();
  return state;
}

function quantity(page: Page) {
  return page.getByRole("textbox", { name: "How many leads?" });
}

function summary(page: Page) {
  return page.getByRole("region", { name: "Summary" });
}

test.describe("New request", () => {
  test("one page: quantity, appointed states, and Options, with a live summary", async ({ page }) => {
    await openNewRequest(page);

    await expect(page.getByText("Any number from 1 to 100,000.", { exact: true })).toBeVisible();
    const states = page.getByRole("group", { name: "Appointed states" });
    await expect(states.getByRole("checkbox", { name: "Florida" })).toBeChecked();
    await expect(states.getByRole("checkbox", { name: "Texas" })).toBeChecked();
    await expect(states.getByRole("link", { name: "Edit appointed states" }))
      .toHaveAttribute("href", "/app/account");
    // The file split of the latest batch is the starting choice.
    await expect(page.getByText("Options · split into files of 10,000 rows")).toBeVisible();

    await quantity(page).fill("50,000");
    const facts = summary(page);
    await expect(facts).toContainText("Leads50,000");
    await expect(facts).toContainText("StatesFL, TX");
    await expect(facts).toContainText("Rate$5.00 per 1,000");
    await expect(facts).toContainText("Cost$250.00");
    await expect(facts).toContainText("Your balance$412.50");
    await expect(facts).toContainText("Balance after$162.50");
    await expect(facts.getByRole("button", { name: "Submit request" })).toBeEnabled();

    // Supply stays hidden (CF3, Q11).
    await expect(page.locator("main")).not.toContainText(/availab|in stock|inventory/i);
  });

  test("submits once and lands on the new batch's page", async ({ page }) => {
    const state = await openNewRequest(page, { submitDelayMs: 400 });

    await quantity(page).fill("25000");
    await page.getByRole("checkbox", { name: "Texas" }).uncheck();
    await summary(page).getByRole("button", { name: "Submit request" }).dblclick();

    await expect(page).toHaveURL(/\/app\/batches\/1043$/);
    await expect(page.getByRole("heading", { level: 1, name: "Batch #1043" })).toBeVisible();
    await expect(page.getByRole("status").filter({ hasText: "Request submitted." })).toBeVisible();
    expect(new Set(state.submissions.map((body) => body.idempotency_key)).size).toBe(1);
    expect(state.submissions[0]).toMatchObject({
      lead_count: 25_000,
      rows_per_file: 10_000,
      state_mode: "selected",
      states: ["FL"],
      expected_lead_rate_cents_per_thousand: 500,
    });
  });

  test("every appointed state and one file send the plain request", async ({ page }) => {
    const state = await openNewRequest(page, { workspace: EMPTY_BATCH_REQUEST_WORKSPACE });

    await expect(page.getByText("Options · one file")).toBeVisible();
    await quantity(page).fill("750");
    await summary(page).getByRole("button", { name: "Submit request" }).click();

    await expect(page).toHaveURL(/\/app\/batches\/1043$/);
    expect(state.submissions[0]).toMatchObject({ lead_count: 750, state_mode: "all_saved", states: [] });
    expect(state.submissions[0]).not.toHaveProperty("rows_per_file");
  });

  test("splits files under Options with a preview", async ({ page }) => {
    const state = await openNewRequest(page, { workspace: EMPTY_BATCH_REQUEST_WORKSPACE });

    await quantity(page).fill("50000");
    await page.getByText("Options · one file").click();
    await page.getByRole("radio", { name: "Split into several files, in one zip" }).check();
    await page.getByRole("textbox", { name: "Rows per file" }).fill("15000");
    await expect(page.getByText("50,000 leads in 4 files of up to 15,000 rows.")).toBeVisible();
    await expect(page.getByText("Options · split into files of 15,000 rows")).toBeVisible();
    await summary(page).getByRole("button", { name: "Submit request" }).click();

    await expect(page).toHaveURL(/\/app\/batches\/1043$/);
    expect(state.submissions[0]).toMatchObject({ lead_count: 50_000, rows_per_file: 15_000 });
  });

  test("keeps invalid requests from leaving the browser", async ({ page }) => {
    const state = await openNewRequest(page);
    const submit = summary(page).getByRole("button", { name: "Submit request" });

    await submit.click();
    await expect(page.getByText("Enter how many leads you want.")).toBeVisible();
    await expect(quantity(page)).toBeFocused();

    await quantity(page).fill("100001");
    await submit.click();
    await expect(page.getByText("Enter between 1 and 100,000 leads.")).toBeVisible();

    await quantity(page).fill("750");
    await page.getByRole("checkbox", { name: "Florida" }).uncheck();
    await page.getByRole("checkbox", { name: "Texas" }).uncheck();
    await submit.click();
    await expect(page.getByText("Choose at least one appointed state.")).toBeVisible();
    expect(state.submissions).toHaveLength(0);
  });

  test("short on credits: buys the difference and Stripe returns to the filled-in form", async ({ page }) => {
    await openNewRequest(page);
    await page.route("https://checkout.stripe.test/**", (route) =>
      route.fulfill({ status: 200, contentType: "text/html", body: "<html><body>Stripe Checkout</body></html>" }),
    );

    await quantity(page).fill("100000");
    await page.getByRole("checkbox", { name: "Texas" }).uncheck();
    const facts = summary(page);
    await expect(facts).toContainText("Cost$500.00");
    await expect(facts).toContainText("Short by$87.50");
    await expect(facts.getByRole("button", { name: "Submit request" })).toBeDisabled();

    const purchase = page.waitForRequest(
      (request) => request.url().includes("/api/me/billing/purchases") && request.method() === "POST",
    );
    // Credits come in whole dollars.
    await facts.getByRole("button", { name: "Buy $88.00 in credits" }).click();
    expect((await purchase).postDataJSON()).toEqual({
      amount_dollars: 88,
      return_to: "/app/new-request?quantity=100000&states=FL&rows_per_file=10000",
    });
    await expect(page).toHaveURL(/checkout\.stripe\.test/);

    // Stripe sends the Customer back to the path the backend validated.
    await page.goto("./new-request?quantity=100000&states=FL&rows_per_file=10000&purchase=success");
    const notice = page.getByRole("status").filter({ hasText: /Credit Purchase/ });
    await expect(notice).toBeVisible();
    await expect(quantity(page)).toHaveValue("100000");
    await expect(page.getByRole("checkbox", { name: "Florida" })).toBeChecked();
    await expect(page.getByRole("checkbox", { name: "Texas" })).not.toBeChecked();
    await expect(page.getByText("Options · split into files of 10,000 rows")).toBeVisible();
    // The address is cleaned so a reload starts fresh; the notice stays.
    await expect(page).toHaveURL(/\/app\/new-request$/);
    await expect(notice).toBeVisible();
  });

  test("a refusal from the backend reads in dollars", async ({ page }) => {
    await openNewRequest(page, {
      refuseSubmission: {
        status: 409,
        detail: "Credit Wallet available balance is insufficient for this Batch Hold. Required 12500 cents; available 200 cents.",
      },
    });

    await quantity(page).fill("25000");
    await summary(page).getByRole("button", { name: "Submit request" }).click();

    await expect(page.getByRole("alert")).toContainText("Required $125.00; available $2.00.");
    await expect(page).toHaveURL(/\/app\/new-request$/);
  });

  test("leaves money out for Customers who are not billed", async ({ page }) => {
    await openNewRequest(page, {}, FREE_CUSTOMER_WALLET);

    await quantity(page).fill("750");
    await expect(summary(page)).toContainText("Leads750");
    await expect(summary(page)).not.toContainText(/Cost|Rate|balance/);
  });

  test("offers the account fix when nothing can be requested yet", async ({ page }) => {
    await openNewRequest(page, { workspace: BLOCKED_BATCH_REQUEST_WORKSPACE });

    await expect(page.getByRole("heading", { level: 2, name: "Add your appointed states first" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Add appointed states" })).toHaveAttribute("href", "/app/account");
    await expect(quantity(page)).toHaveCount(0);
  });

  test("New request is the current destination", async ({ page }) => {
    await openNewRequest(page);

    await expect(
      page.getByRole("navigation", { name: "Customer" }).getByRole("link", { name: "New request" }),
    ).toHaveAttribute("aria-current", "page");
  });
});

test.describe("Old request-form links", () => {
  test("a Stripe return to the old form opens New request filled in", async ({ page }) => {
    await openNewRequest(
      page,
      {},
      NORTHSTAR_WALLET,
      "./requests?quantity=750&states=FL&rows_per_file=300&purchase=cancelled",
    );

    await expect(page.getByRole("status").filter({ hasText: "Credit Purchase cancelled" })).toBeVisible();
    await expect(quantity(page)).toHaveValue("750");
    await expect(page.getByRole("checkbox", { name: "Texas" })).not.toBeChecked();
    await expect(page.getByText("Options · split into files of 300 rows")).toBeVisible();
  });

  test("the bare old address opens New request", async ({ page }) => {
    await openNewRequest(page, { workspace: BATCH_REQUEST_WORKSPACE }, NORTHSTAR_WALLET, "./requests");

    await expect(page).toHaveURL(/\/app\/new-request$/);
  });
});
