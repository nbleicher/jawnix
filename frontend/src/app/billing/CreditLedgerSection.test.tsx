import { describe, expect, it } from "vitest";

import { ledgerRowsWithBalance } from "./CreditLedgerSection";
import type { CreditWallet } from "./wallet";

const WALLET: CreditWallet = {
  customerId: 7,
  billingEnabled: true,
  leadRateCentsPerThousand: 500,
  balanceCents: 10_000,
  activeHoldsCents: 375,
  availableBalanceCents: 9_625,
  purchases: [],
  // Newest-first, matching wallet_view's API ordering.
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

describe("ledgerRowsWithBalance", () => {
  it("derives a running balance that reconciles to the wallet balance", () => {
    const rows = ledgerRowsWithBalance(WALLET);
    // Newest-first: $100.00 (wallet) → $96.25 → $100.00 after the purchase.
    expect(rows.map((row) => row.balanceAfterCents)).toEqual([
      10_000, 9_625, 10_000,
    ]);
  });

  it("starts from the wallet balance on the newest entry", () => {
    const rows = ledgerRowsWithBalance(WALLET);
    expect(rows[0]?.balanceAfterCents).toBe(WALLET.balanceCents);
  });
});
