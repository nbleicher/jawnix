# Re-admit billing as prepaid credits, not invoicing

The platform rebuild deliberately excluded billing (`JAWNIX_ENABLE_BILLING=false`; the legacy
invoice/Stripe app survives only as a contract reference). We are bringing billing back into the
active domain in a different shape: per-Customer prepaid credits rather than invoices. An
administrator switches billing on per Customer with a required flat Lead Rate; Customers top up a
dollar-denominated Credit Wallet through Stripe Checkout, credited only by the verified webhook;
a billed request places a Batch Hold at submission and captures at the same transaction boundary
that commits distribution (ADR 0003), so money moves exactly when leads do.

## Considered Options

- Charging at submission or at approval was rejected: submission-time charging takes money for
  batches that may be rejected or wait indefinitely, and approval-time charging lets pending
  requests overdraw the wallet.
- Lead-count credits were rejected in favor of dollars: rate changes would otherwise force a
  migration of every outstanding balance.
- In-app card refunds were rejected: the Credit Ledger is append-only; refunds happen in the
  Stripe dashboard and reconcile as administrator adjustments.

## Consequences

Legacy invoicing and financial reporting remain outside the domain. A request's billed-or-free
status is frozen at submission, so flipping a Customer's toggle never reprices in-flight work.

Stripe payments apply only to Database A (DB1, the operator's inventory). Database B (DB2,
Managed Administrator uploads) cannot start Stripe purchases or new billed requests. Switching
databases retains the saved Database A price and wallet balance. Existing requests retain their
submitted terms. The operator enters each Customer's price in dollars per lead with sub-cent
precision; integer cents per 1,000 leads remain the internal accounting unit.
New Database A Customers receive the deployment's configured starting rate when billing is
enabled. This initializes their saved per-Customer configuration once; subsequent price changes,
free-account overrides, and restored historical configurations are preserved.
Submission compares the Customer's reviewed price with the current price under the Customer
lock. A changed price requires another review before any request or hold is created. Replaying
an already submitted request still returns its original terms.
