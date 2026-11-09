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

test.describe("Account Credit Ledger", () => {
  test("lists every ledger entry for a Billed Customer", async ({ page }) => {
    await mockCustomerAuth(page, {
      billing: { wallet: BILLED_CUSTOMER_WALLET },
    });
    await page.goto("./account");

    await expect(
      page.getByRole("heading", { level: 2, name: "Credit Wallet" }),
    ).toBeVisible();
    await expect(page.getByText("$96.25").first()).toBeVisible();

    const ledger = page.getByRole("region", { name: "Credit Ledger" });
    await expect(
      ledger.getByText("Credit Purchase", { exact: true }),
    ).toBeVisible();
    await expect(
      ledger.getByText("Batch Charge", { exact: true }),
    ).toBeVisible();
    await expect(
      ledger.getByText("Adjustment", { exact: true }),
    ).toBeVisible();
    await expect(ledger.getByText("Reconcile Stripe refund")).toBeVisible();
    await expect(ledger.getByText("+$100.00")).toBeVisible();
    await expect(ledger.getByText("−$3.75")).toBeVisible();

    // Units live in the header ("Date (UTC)"), not repeated in every cell.
    await expect(
      ledger.getByRole("columnheader", { name: "Date (UTC)" }),
    ).toBeVisible();

    // The full reference is reachable without hover: the short form toggles
    // the complete id inline.
    const reference = ledger.getByRole("button", { name: /CP-DDDDDDDD…/ });
    await expect(reference).toHaveAttribute("aria-expanded", "false");
    await reference.click();
    await expect(reference).toHaveAttribute("aria-expanded", "true");
    await expect(
      ledger.getByText("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
    ).toBeVisible();

    await expect(
      page.getByRole("heading", { level: 2, name: "Recent purchase attempts" }),
    ).toBeVisible();
  });

  test("shows processing until the purchase is credited", async ({ page }) => {
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
    await page.goto("./account?purchase=success");

    await expect(
      page.getByRole("status").filter({ hasText: /processing/i }),
    ).toBeVisible();

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

    await expect(
      page.getByRole("status").filter({ hasText: /complete/i }),
    ).toBeVisible({ timeout: 10_000 });
  });

  test("Free Customers never see the Credit Ledger on Account", async ({
    page,
  }) => {
    await mockCustomerAuth(page, {
      billing: { wallet: FREE_CUSTOMER_WALLET },
    });
    await page.goto("./account");

    await expect(
      page.getByRole("heading", { level: 1, name: "Account" }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "Credit Ledger" }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("heading", { name: "Credit Wallet" }),
    ).toHaveCount(0);
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
