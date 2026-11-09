import type { BatchIdentity } from "./batchLabel";

/**
 * Account's Billing tab, read in one request (`/api/me/billing/summary`).
 *
 * The header's Credit Wallet keeps its own read; this one carries what only
 * the Billing tab shows: what is held and for which batches, spending in the
 * last 30 days, and the history with every batch named by its number.
 */
export interface CustomerBillingBatch extends BatchIdentity {
  number: number;
  /** The batch's page in the portal. */
  href: string;
}

export type CustomerBillingEntryKind = "purchase" | "charge" | "hold" | "adjustment";

export interface CustomerBillingEntry {
  id: string;
  kind: CustomerBillingEntryKind;
  occurred_at: string;
  /** Signed: credits positive, charges and holds negative. */
  amount_cents: number;
  /** The available balance once this row had happened. */
  balance_after_cents: number;
  batch: CustomerBillingBatch | null;
  /** The reason given for an adjustment. */
  note: string | null;
}

export interface CustomerBilling {
  available_balance_cents: number;
  held_cents: number;
  held_for: CustomerBillingBatch[];
  lead_rate_cents_per_thousand: number | null;
  spent_since: string;
  spent_cents: number;
  spent_batches: number;
  history: CustomerBillingEntry[];
  /** False when older rows were left off. */
  history_complete: boolean;
}

const SUMMARY_PATH = "/api/me/billing/summary";

/** The Billing tab's read, or null when the Customer is not billed. */
export async function loadCustomerBilling(): Promise<CustomerBilling | null> {
  const response = await fetch(SUMMARY_PATH, { credentials: "same-origin" });
  if (response.status === 404) return null;
  if (!response.ok) {
    throw new Error("Billing could not be loaded. Please try again.");
  }
  return response.json() as Promise<CustomerBilling>;
}
