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
    await page.goto("./overview");

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

    await page.goto("./overview");
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
    expect(posted.postDataJSON()).toEqual({ amount_dollars: 40 });

    await expect(page).toHaveURL(/checkout\.stripe\.test/);
  });

  test("the purchase dialog refuses fractional and sub-dollar amounts", async ({
    page,
  }) => {
    await mockCustomerAuth(page, {
      billing: { wallet: BILLED_CUSTOMER_WALLET },
    });
    await page.goto("./requests");

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
      ledger.getByRole("heading", { name: "Credit Purchase" }),
    ).toBeVisible();
    await expect(
      ledger.getByRole("heading", { name: "Batch Charge" }),
    ).toBeVisible();
    await expect(
      ledger.getByRole("heading", { name: "Adjustment" }),
    ).toBeVisible();
    await expect(ledger.getByText("Reconcile Stripe refund")).toBeVisible();
    await expect(ledger.getByText("+$100.00")).toBeVisible();
    await expect(ledger.getByText("−$3.75")).toBeVisible();

    await expect(
      page.getByRole("heading", { level: 2, name: "Processing purchases" }),
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
  test("review names cost, Batch Hold, and available balance", async ({
    page,
  }) => {
    await mockCustomerAuth(page, {
      billing: { wallet: BILLED_CUSTOMER_WALLET },
    });
    await mockBatchRequests(page, { workspace: EMPTY_BATCH_REQUEST_WORKSPACE });
    await page.goto("./requests");

    await page.getByRole("spinbutton", { name: /How many leads/ }).fill("750");
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByRole("button", { name: "Continue" }).click();

    await expect(
      page.getByRole("heading", { name: "Review your request" }),
    ).toBeVisible();
    await expect(
      page.getByText("750 × $0.005 per lead = $3.75", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText(/\$3\.75 reserved from your Credit Wallet/),
    ).toBeVisible();
    await expect(page.locator(".request-flow__review")).toContainText("$96.25");
  });

  test("refuses submit when available balance cannot cover the Batch Hold", async ({
    page,
  }) => {
    await mockCustomerAuth(page, {
      billing: { wallet: UNDERFUNDED_WALLET },
    });
    const state = await mockBatchRequests(page, {
      workspace: EMPTY_BATCH_REQUEST_WORKSPACE,
    });
    await page.goto("./requests");

    await page.getByRole("spinbutton", { name: /How many leads/ }).fill("750");
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByRole("button", { name: "Continue" }).click();

    await expect(page.getByRole("alert")).toContainText(
      /does not have enough available balance for this Batch Hold/,
    );
    await expect(page.getByRole("alert")).toContainText("Required $3.75");
    await expect(page.getByRole("alert")).toContainText("available $2.00");
    await expect(
      page.getByRole("button", { name: "Submit request" }),
    ).toBeDisabled();
    expect(state.submissions).toHaveLength(0);
  });
});
