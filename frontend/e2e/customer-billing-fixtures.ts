import type { Page, Route } from "@playwright/test";

export interface MockCreditWallet {
  customerId: number;
  billingEnabled: boolean;
  leadRateCentsPerThousand: number | null;
  balanceCents: number;
  activeHoldsCents: number;
  availableBalanceCents: number;
  purchases: Array<{
    id: string;
    amountCents: number;
    status: "processing" | "completed";
    createdAt: string;
    completedAt: string | null;
  }>;
  ledger: Array<{
    id: string;
    kind: "purchase" | "batch_charge" | "admin_adjustment";
    amountCents: number;
    reason: string | null;
    actor: string | null;
    batchRequestId: string | null;
    createdAt: string;
  }>;
}

function json(route: Route, value: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(value),
  });
}

export const FREE_CUSTOMER_WALLET: MockCreditWallet = {
  customerId: 7,
  billingEnabled: false,
  leadRateCentsPerThousand: null,
  balanceCents: 0,
  activeHoldsCents: 0,
  availableBalanceCents: 0,
  purchases: [],
  ledger: [],
};

export const BILLED_CUSTOMER_WALLET: MockCreditWallet = {
  customerId: 7,
  billingEnabled: true,
  leadRateCentsPerThousand: 500,
  balanceCents: 10_000,
  activeHoldsCents: 375,
  availableBalanceCents: 9_625,
  purchases: [
    {
      id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
      amountCents: 2_500,
      status: "processing",
      createdAt: "2026-08-03T14:00:00Z",
      completedAt: null,
    },
  ],
  // Newest-first, matching wallet_view's ordering (created_at desc, id desc).
  ledger: [
    {
      id: "ffffffff-ffff-4fff-8fff-ffffffffffff",
      kind: "admin_adjustment",
      amountCents: 375,
      reason: "Reconcile Stripe refund",
      actor: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      batchRequestId: null,
      createdAt: "2026-08-02T15:00:00Z",
    },
    {
      id: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
      kind: "batch_charge",
      amountCents: -375,
      reason: null,
      actor: null,
      batchRequestId: "11111111-1111-4111-8111-111111111111",
      createdAt: "2026-08-02T12:00:00Z",
    },
    {
      id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
      kind: "purchase",
      amountCents: 10_000,
      reason: null,
      actor: null,
      batchRequestId: null,
      createdAt: "2026-08-01T12:00:00Z",
    },
  ],
};

/** Wallet with too little available balance for a 750-lead hold at $0.005/lead. */
export const UNDERFUNDED_WALLET: MockCreditWallet = {
  ...BILLED_CUSTOMER_WALLET,
  balanceCents: 200,
  activeHoldsCents: 0,
  availableBalanceCents: 200,
  purchases: [],
  ledger: [
    {
      id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
      kind: "purchase",
      amountCents: 200,
      reason: null,
      actor: null,
      batchRequestId: null,
      createdAt: "2026-08-01T12:00:00Z",
    },
  ],
};

/** Account's Billing tab (`/api/me/billing/summary`). */
export interface MockBillingSummary {
  available_balance_cents: number;
  held_cents: number;
  held_for: Array<{ id: string; number: number; href: string }>;
  lead_rate_cents_per_thousand: number | null;
  spent_since: string;
  spent_cents: number;
  spent_batches: number;
  history: Array<{
    id: string;
    kind: "purchase" | "charge" | "hold" | "adjustment";
    occurred_at: string;
    amount_cents: number;
    balance_after_cents: number;
    batch: { id: string; number: number; href: string } | null;
    note: string | null;
  }>;
  history_complete: boolean;
}

/** Batch numbers for the ledger's batch ids, so derived histories name them. */
const FIXTURE_BATCH_NUMBERS: Record<string, number> = {
  "11111111-1111-4111-8111-111111111111": 1038,
};

function fixtureBatch(id: string) {
  return { id, number: FIXTURE_BATCH_NUMBERS[id] ?? 1001, href: `/app/batches/${id}` };
}

/** The Billing tab that agrees with a header wallet: its ledger, newest
 *  first, with any active hold on top as "Held for batch #1042". */
export function billingSummaryFor(wallet: MockCreditWallet): MockBillingSummary {
  const held = wallet.activeHoldsCents
    ? [{ id: "42424242-4242-4242-8242-424242424242", number: 1042, href: "/app/batches/42424242-4242-4242-8242-424242424242" }]
    : [];
  const rows: Array<Omit<MockBillingSummary["history"][number], "balance_after_cents">> = [
    ...held.map((batch) => ({
      id: `hold:${batch.id}`,
      kind: "hold" as const,
      occurred_at: "2026-08-03T15:30:00Z",
      amount_cents: -wallet.activeHoldsCents,
      batch,
      note: null,
    })),
    ...wallet.ledger.map((entry) => ({
      id: entry.id,
      kind: ({ purchase: "purchase", batch_charge: "charge", admin_adjustment: "adjustment" } as const)[entry.kind],
      occurred_at: entry.createdAt,
      amount_cents: entry.amountCents,
      batch: entry.batchRequestId ? fixtureBatch(entry.batchRequestId) : null,
      note: entry.kind === "admin_adjustment" ? entry.reason : null,
    })),
  ];
  let balance = wallet.availableBalanceCents;
  const history = rows.map((row) => {
    const withBalance = { ...row, balance_after_cents: balance };
    balance -= row.amount_cents;
    return withBalance;
  });
  const charges = wallet.ledger.filter((entry) => entry.kind === "batch_charge");
  return {
    available_balance_cents: wallet.availableBalanceCents,
    held_cents: wallet.activeHoldsCents,
    held_for: held,
    lead_rate_cents_per_thousand: wallet.leadRateCentsPerThousand,
    spent_since: "2026-07-04T00:00:00Z",
    spent_cents: -charges.reduce((sum, entry) => sum + entry.amountCents, 0),
    spent_batches: charges.length,
    history,
    history_complete: true,
  };
}

export interface CustomerBillingMockOptions {
  wallet?: MockCreditWallet | (() => MockCreditWallet);
  /** The Billing tab's read; derived from the wallet when absent. */
  summary?: MockBillingSummary | (() => MockBillingSummary);
  walletStatus?: number;
  purchaseCheckoutUrl?: string;
  purchaseStatus?: number;
  purchaseDetail?: string;
}

export interface CustomerBillingMockState {
  walletReads: number;
  purchases: Array<{ amount_dollars: number }>;
  wallet: MockCreditWallet;
}

/**
 * Layer over mockCustomerAuth. Defaults to a Free Customer wallet so existing
 * specs that do not care about billing stay free of Credit Wallet chrome.
 */
export async function mockCustomerBilling(
  page: Page,
  options: CustomerBillingMockOptions = {},
): Promise<CustomerBillingMockState> {
  const state: CustomerBillingMockState = {
    walletReads: 0,
    purchases: [],
    wallet: structuredClone(
      typeof options.wallet === "function"
        ? options.wallet()
        : (options.wallet ?? FREE_CUSTOMER_WALLET),
    ),
  };
  const currentWallet = () =>
    typeof options.wallet === "function"
      ? options.wallet()
      : state.wallet;

  await page.route(/\/api\/me\/billing$/, (route) => {
    if (route.request().method() !== "GET") {
      return route.fallback();
    }
    state.walletReads += 1;
    if (options.walletStatus && options.walletStatus !== 200) {
      return json(
        route,
        { detail: "Customer was not found." },
        options.walletStatus,
      );
    }
    const wallet = currentWallet();
    state.wallet = structuredClone(wallet);
    return json(route, wallet);
  });

  await page.route(/\/api\/me\/billing\/summary$/, (route) => {
    const wallet = currentWallet();
    if (!wallet.billingEnabled) {
      return json(route, { detail: "Billing is not set up for this account." }, 404);
    }
    const summary =
      typeof options.summary === "function" ? options.summary() : options.summary;
    return json(route, summary ?? billingSummaryFor(wallet));
  });

  await page.route(/\/api\/me\/billing\/purchases$/, async (route) => {
    if (route.request().method() !== "POST") {
      return route.fallback();
    }
    const body = route.request().postDataJSON() as { amount_dollars?: number };
    state.purchases.push({ amount_dollars: Number(body.amount_dollars) });
    if (options.purchaseStatus && options.purchaseStatus !== 201) {
      return json(
        route,
        {
          detail:
            options.purchaseDetail
            ?? "Credit Purchases must be whole dollars with a $1 floor.",
        },
        options.purchaseStatus,
      );
    }
    const amountCents = Number(body.amount_dollars) * 100;
    const purchase = {
      id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      amountCents,
      status: "processing" as const,
      createdAt: "2026-08-03T15:00:00Z",
      completedAt: null,
    };
    state.wallet = {
      ...state.wallet,
      purchases: [purchase, ...state.wallet.purchases],
    };
    return json(
      route,
      {
        checkoutUrl:
          options.purchaseCheckoutUrl
          ?? "https://checkout.stripe.test/pay/cs_test_e2e",
        purchase,
      },
      201,
    );
  });

  return state;
}
