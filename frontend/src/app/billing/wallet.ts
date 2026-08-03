export type CreditPurchaseStatus = "processing" | "completed";

export type CreditLedgerKind =
  | "purchase"
  | "batch_charge"
  | "admin_adjustment";

export interface CreditPurchase {
  id: string;
  amountCents: number;
  status: CreditPurchaseStatus;
  createdAt: string;
  completedAt: string | null;
}

export interface CreditLedgerEntry {
  id: string;
  kind: CreditLedgerKind;
  amountCents: number;
  reason: string | null;
  actor: string | null;
  batchRequestId: string | null;
  createdAt: string;
}

export interface CreditWallet {
  customerId: number;
  billingEnabled: boolean;
  leadRateCentsPerThousand: number | null;
  balanceCents: number;
  activeHoldsCents: number;
  availableBalanceCents: number;
  purchases: CreditPurchase[];
  ledger: CreditLedgerEntry[];
}

export interface CreditPurchaseStart {
  checkoutUrl: string;
  purchase: CreditPurchase;
}

const BILLING_PATH = "/api/me/billing";
const PURCHASES_PATH = "/api/me/billing/purchases";

function csrf(): string {
  const item = document.cookie
    .split(";")
    .map((value) => value.trim())
    .find((value) => value.startsWith("jawnix_csrf="));
  return decodeURIComponent(item?.split("=", 2)[1] ?? "");
}

interface ErrorBody {
  detail?: string | { msg?: string }[];
}

function refusal(body: ErrorBody, fallback: string): string {
  if (typeof body.detail === "string") return body.detail;
  const first = Array.isArray(body.detail) ? body.detail[0]?.msg : undefined;
  return first ?? fallback;
}

/**
 * Load the Credit Wallet for the signed-in Customer.
 *
 * Returns null when there is no Customer mapping (404) so Free/unmapped
 * accounts simply show no billing UI.
 */
export async function loadCreditWallet(): Promise<CreditWallet | null> {
  const response = await fetch(BILLING_PATH, { credentials: "same-origin" });
  if (response.status === 404) return null;
  if (response.status === 401 || response.status === 403) {
    return null;
  }
  if (!response.ok) {
    throw new Error("The Credit Wallet could not be loaded. Please try again.");
  }
  return response.json() as Promise<CreditWallet>;
}

/** Open a Stripe Checkout session for a whole-dollar Credit Purchase. */
export async function startCreditPurchase(
  amountDollars: number,
): Promise<CreditPurchaseStart> {
  const response = await fetch(PURCHASES_PATH, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": csrf(),
    },
    credentials: "same-origin",
    body: JSON.stringify({ amount_dollars: amountDollars }),
  });
  const parsed = (await response.json().catch(() => ({}))) as ErrorBody &
    Partial<CreditPurchaseStart>;
  if (!response.ok) {
    throw new Error(
      refusal(
        parsed,
        "The Credit Purchase could not be started. Please try again.",
      ),
    );
  }
  if (!parsed.checkoutUrl || !parsed.purchase) {
    throw new Error(
      "The Credit Purchase could not be started. Please try again.",
    );
  }
  return {
    checkoutUrl: parsed.checkoutUrl,
    purchase: parsed.purchase,
  };
}

export function hasProcessingPurchase(wallet: CreditWallet): boolean {
  return wallet.purchases.some((purchase) => purchase.status === "processing");
}

export const LEDGER_KIND_LABEL: Record<CreditLedgerKind, string> = {
  purchase: "Credit Purchase",
  batch_charge: "Batch Charge",
  admin_adjustment: "Adjustment",
};
