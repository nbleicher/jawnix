/** Format integer cents as a USD currency string. */
export function formatCents(cents: number): string {
  return (cents / 100).toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
  });
}

/**
 * Round a lead quantity at the Lead Rate with a 1¢ positive-batch floor.
 *
 * Mirrors `batch_cost_cents` in jawnix.billing: half-up without float money.
 */
export function batchCostCents(
  leadCount: number,
  leadRateCentsPerThousand: number,
): number {
  const rounded = Math.trunc(
    (leadCount * leadRateCentsPerThousand + 500) / 1_000,
  );
  return leadCount > 0 ? Math.max(1, rounded) : 0;
}

/** Lead Rate stored as cents per 1,000 leads → dollars-per-lead label. */
export function formatLeadRate(centsPerThousand: number): string {
  const perLead = centsPerThousand / 100_000;
  return `${perLead.toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 3,
    maximumFractionDigits: 5,
  })} per lead`;
}

/**
 * Map a backend Credit Wallet refusal into a Customer-facing sentence that
 * still names the Batch Hold and Available Balance in dollars.
 */
export function formatBalanceRefusal(detail: string): string {
  const match = detail.match(
    /Required (\d+) cents; available (-?\d+) cents\./,
  );
  if (!match) return detail;
  const required = Number(match[1]);
  const available = Number(match[2]);
  return (
    `The Credit Wallet does not have enough available balance for this Batch Hold. ` +
    `Required ${formatCents(required)}; available ${formatCents(available)}.`
  );
}
