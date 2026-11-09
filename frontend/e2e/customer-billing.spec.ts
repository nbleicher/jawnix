import { expect, test } from "@playwright/test";

import {
  FREE_CUSTOMER_WALLET,
  UNDERFUNDED_WALLET,
} from "./customer-billing-fixtures";
import { mockCustomerAuth } from "./customer-auth-fixtures";
import {
  EMPTY_BATCH_REQUEST_WORKSPACE,
  mockBatchRequests,
} from "./customer-requests-fixtures";
import { BILLED_CUSTOMER_WALLET } from "./customer-billing-fixtures";

test.describe("Credit Wallet widget", () => {
  test("Free Customers never see Credit Wallet chrome", async ({ page }) => {
    await mockCustomerAuth(page, {
      billing: { wallet: FREE_CUSTOMER_WALLET },
    });
    await page.goto("./home");

    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await expect(page.getByLabel("Credit Wallet summary")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Buy credits" })).toHaveCount(
      0,
    );
  });

  test("Billed Customers see available balance and can start a purchase", async ({
    page,
  }) => {
    const walletBox = { current: structuredClone(BILLED_CUSTOMER_WALLET) };
    await mockCustomerAuth(page, {
      billing: {
        wallet: () => walletBox.current,
        purchaseCheckoutUrl: "https://checkout.stripe.test/pay/cs_test_buy",
      },
    });

    await page.route("https://checkout.stripe.test/**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "text/html",
        body: "<html><body>Stripe Checkout</body></html>",
      }),
    );

    await page.goto("./home");
    const wallet = page.getByLabel("Credit Wallet summary");
    await expect(wallet).toBeVisible();
    await expect(wallet.getByText("$96.25")).toBeVisible();
    await expect(wallet.getByText("Processing")).toBeVisible();

    await page.getByRole("button", { name: "Buy credits" }).click();
    const dialog = page.getByRole("dialog", { name: "Buy credits" });
    await expect(dialog).toBeVisible();
    await dialog.getByLabel("Amount in dollars").fill("40");

    const purchaseRequest = page.waitForRequest(
      (request) =>
        request.url().includes("/api/me/billing/purchases")
        && request.method() === "POST",
    );
    await dialog.getByRole("button", { name: "Continue to Stripe" }).click();
    const posted = await purchaseRequest;
    // Checkout returns to the page the purchase started on.
    expect(posted.postDataJSON()).toEqual({
      amount_dollars: 40,
      return_to: "/app/home",
    });

    await expect(page).toHaveURL(/checkout\.stripe\.test/);
  });

  test("the purchase dialog refuses fractional and sub-dollar amounts", async ({
    page,
  }) => {
    await mockCustomerAuth(page, {
      billing: { wallet: BILLED_CUSTOMER_WALLET },
    });
    await page.goto("./batches");

    await page.getByRole("button", { name: "Buy credits" }).click();
    const dialog = page.getByRole("dialog", { name: "Buy credits" });
    await dialog.getByLabel("Amount in dollars").fill("0");
    await dialog.getByRole("button", { name: "Continue to Stripe" }).click();
    await expect(dialog).toContainText(/whole-dollar amount of at least \$1/);
  });
});

test.describe("Account Billing", () => {
  test("shows balance, held, rate, and spending, with history linked to batches", async ({
    page,
  }) => {
    await mockCustomerAuth(page, {
      billing: {
        wallet: BILLED_CUSTOMER_WALLET,
        purchaseCheckoutUrl: "https://checkout.stripe.test/pay/cs_test_account",
      },
    });
    await page.route("https://checkout.stripe.test/**", (route) =>
      route.fulfill({ status: 200, contentType: "text/html", body: "<html><body>Stripe Checkout</body></html>" }),
    );
    await page.goto("./account/billing");

    const tile = (label: string) =>
      page.getByLabel("Billing", { exact: true }).locator(".jx-kpi").filter({ hasText: label });
    await expect(tile("Balance")).toContainText("$96.25");
    await expect(tile("Balance")).toContainText("Available for new requests");
    await expect(tile("Held")).toContainText("$3.75");
    await expect(tile("Held")).toContainText("For #1042 until it is delivered");
    await expect(tile("Rate")).toContainText("$5.00");
    await expect(tile("Rate")).toContainText("Per 1,000 leads");
    await expect(tile("Spent · 30 days")).toContainText("$3.75");
    await expect(tile("Spent · 30 days")).toContainText("1 batch");

    const history = page.getByRole("table", { name: "Billing history" });
    const rows = history.getByRole("row");
    await expect(rows).toHaveCount(5);
    await expect(rows.nth(1)).toContainText("Held for batch #1042");
    await expect(rows.nth(1)).toContainText("−$3.75");
    await expect(rows.nth(1)).toContainText("$96.25");
    await expect(rows.nth(2)).toContainText("Adjustment · Reconcile Stripe refund");
    await expect(rows.nth(2)).toContainText("+$3.75");
    await expect(rows.nth(3)).toContainText("Charged for batch #1038");
    await expect(rows.nth(4)).toContainText("Credits bought");
    await expect(rows.nth(4)).toContainText("+$100.00");
    // Every batch links to its page; no internal references are shown.
    await expect(history.getByRole("link", { name: "batch #1038" })).toHaveAttribute(
      "href",
      "/app/batches/11111111-1111-4111-8111-111111111111",
    );
    await expect(history).not.toContainText(/CP-|BC-|REQ-/);

    // Buy credits on the tab returns to the tab.
    await page.getByRole("main").getByRole("button", { name: "Buy credits" }).click();
    const dialog = page.getByRole("dialog", { name: "Buy credits" });
    await dialog.getByLabel("Amount in dollars").fill("40");
    const purchase = page.waitForRequest(
      (request) => request.url().includes("/api/me/billing/purchases") && request.method() === "POST",
    );
    await dialog.getByRole("button", { name: "Continue to Stripe" }).click();
    expect((await purchase).postDataJSON()).toEqual({
      amount_dollars: 40,
      return_to: "/app/account/billing",
    });
  });

  test("reads the history again when a processing purchase is credited", async ({ page }) => {
    const walletBox: { current: typeof BILLED_CUSTOMER_WALLET } = {
      current: {
        ...BILLED_CUSTOMER_WALLET,
        purchases: [
          {
            id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
            amountCents: 4_000,
            status: "processing",
            createdAt: "2026-08-03T14:00:00Z",
            completedAt: null,
          },
        ],
      },
    };
    await mockCustomerAuth(page, {
      billing: { wallet: () => walletBox.current },
    });
    await page.goto("./account/billing?purchase=success");

    await expect(page.getByRole("status").filter({ hasText: /processing/i })).toBeVisible();
    const history = page.getByRole("table", { name: "Billing history" });
    await expect(history.getByText("+$40.00")).toHaveCount(0);

    walletBox.current = {
      ...walletBox.current,
      balanceCents: 14_000,
      availableBalanceCents: 13_625,
      purchases: [
        {
          id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
          amountCents: 4_000,
          status: "completed",
          createdAt: "2026-08-03T14:00:00Z",
          completedAt: "2026-08-03T14:01:00Z",
        },
      ],
      ledger: [
        {
          id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
          kind: "purchase",
          amountCents: 4_000,
          reason: null,
          actor: null,
          batchRequestId: null,
          createdAt: "2026-08-03T14:01:00Z",
        },
        ...walletBox.current.ledger,
      ],
    };

    await expect(page.getByRole("status").filter({ hasText: /complete/i })).toBeVisible({ timeout: 10_000 });
    await expect(history.getByText("+$40.00")).toBeVisible();
    await expect(page.locator(".jx-kpi").filter({ hasText: "Balance" })).toContainText("$136.25");
  });

  test("Free Customers have no Billing tab, and its address says why", async ({ page }) => {
    await mockCustomerAuth(page, {
      billing: { wallet: FREE_CUSTOMER_WALLET },
    });
    await page.goto("./account");

    await expect(page.getByRole("heading", { level: 1, name: "Account" })).toBeVisible();
    await expect(
      page.getByRole("navigation", { name: "Account" }).getByRole("link", { name: "Billing" }),
    ).toHaveCount(0);

    await page.goto("./account/billing");
    await expect(page.getByText("This account is not billed per lead")).toBeVisible();
    await expect(page.getByRole("table", { name: "Billing history" })).toHaveCount(0);
  });
});

test.describe("Billed request submission", () => {
  test("the summary names cost, balance, and the balance after", async ({
    page,
  }) => {
    await mockCustomerAuth(page, {
      billing: { wallet: BILLED_CUSTOMER_WALLET },
    });
    const state = await mockBatchRequests(page, { workspace: EMPTY_BATCH_REQUEST_WORKSPACE });
    await page.goto("./new-request");

    await page.getByRole("textbox", { name: "How many leads?" }).fill("750");
    const summary = page.getByRole("region", { name: "Summary" });
    await expect(summary).toContainText("Rate$5.00 per 1,000");
    await expect(summary).toContainText("Cost$3.75");
    await expect(summary).toContainText("Your balance$96.25");
    await expect(summary).toContainText("Balance after$92.50");
    await summary.getByRole("button", { name: "Submit request" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Batch #1043" })).toBeVisible();
    expect(state.submissions[0]).toMatchObject({ expected_lead_rate_cents_per_thousand: 500 });
  });

  test("refuses submit when the balance cannot cover the cost", async ({
    page,
  }) => {
    await mockCustomerAuth(page, {
      billing: { wallet: UNDERFUNDED_WALLET },
    });
    const state = await mockBatchRequests(page, {
      workspace: EMPTY_BATCH_REQUEST_WORKSPACE,
    });
    await page.goto("./new-request");

    await page.getByRole("textbox", { name: "How many leads?" }).fill("750");
    const summary = page.getByRole("region", { name: "Summary" });
    await expect(summary).toContainText("Cost$3.75");
    await expect(summary).toContainText("Your balance$2.00");
    await expect(summary).toContainText("Short by$1.75");
    await expect(summary.getByRole("button", { name: "Buy $2.00 in credits" })).toBeVisible();
    await expect(summary.getByRole("button", { name: "Submit request" })).toBeDisabled();
    expect(state.submissions).toHaveLength(0);
  });
});

test.describe("Returning from Stripe Checkout", () => {
  test("Buy credits from the header during a request returns to the filled-in form", async ({
    page,
  }) => {
    await mockCustomerAuth(page, {
      billing: {
        wallet: UNDERFUNDED_WALLET,
        purchaseCheckoutUrl: "https://checkout.stripe.test/pay/cs_test_return",
      },
    });
    await mockBatchRequests(page, { workspace: EMPTY_BATCH_REQUEST_WORKSPACE });
    await page.route("https://checkout.stripe.test/**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "text/html",
        body: "<html><body>Stripe Checkout</body></html>",
      }),
    );
    await page.goto("./new-request");

    await page.getByRole("textbox", { name: "How many leads?" }).fill("750");
    await page.getByRole("checkbox", { name: "Texas" }).uncheck();
    await page.getByText("Options · one file").click();
    await page.getByRole("radio", { name: "Split into several files, in one zip" }).check();
    await page.getByRole("textbox", { name: "Rows per file" }).fill("300");
    await page.getByRole("banner").getByRole("button", { name: "Buy credits" }).click();
    const dialog = page.getByRole("dialog", { name: "Buy credits" });
    const purchaseRequest = page.waitForRequest(
      (request) =>
        request.url().includes("/api/me/billing/purchases")
        && request.method() === "POST",
    );
    await dialog.getByRole("button", { name: "Continue to Stripe" }).click();
    expect((await purchaseRequest).postDataJSON()).toEqual({
      amount_dollars: 25,
      return_to: "/app/new-request?quantity=750&states=FL&rows_per_file=300",
    });
    await expect(page).toHaveURL(/checkout\.stripe\.test/);

    // Stripe sends the Customer back to the URL the backend validated.
    await page.goto(
      "./new-request?quantity=750&states=FL&rows_per_file=300&purchase=success",
    );

    const notice = page.getByRole("status").filter({ hasText: /Credit Purchase/ });
    await expect(notice).toBeVisible();
    const title = page.getByRole("heading", { level: 1, name: "New request" });
    expect((await notice.boundingBox())!.y).toBeLessThan(
      (await title.boundingBox())!.y,
    );
    await expect(page.getByRole("textbox", { name: "How many leads?" })).toHaveValue("750");
    await expect(page.getByRole("checkbox", { name: "Texas" })).not.toBeChecked();
    await expect(page.getByText("Options · split into files of 300 rows")).toBeVisible();
    // The address is cleaned so a reload starts fresh; the notice stays.
    await expect(page).toHaveURL(/\/app\/new-request$/);
    await expect(notice).toBeVisible();
  });

  test("a cancelled checkout says so at the top of the page it started on", async ({
    page,
  }) => {
    await mockCustomerAuth(page, {
      billing: { wallet: BILLED_CUSTOMER_WALLET },
    });
    await mockBatchRequests(page, { workspace: EMPTY_BATCH_REQUEST_WORKSPACE });
    await page.goto("./new-request?quantity=750&purchase=cancelled");

    await expect(
      page.getByRole("status").filter({ hasText: "Credit Purchase cancelled" }),
    ).toHaveText("Credit Purchase cancelled. Your Credit Wallet was not charged.");
    await expect(page.getByRole("textbox", { name: "How many leads?" })).toHaveValue("750");
  });
});
