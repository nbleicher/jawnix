# Make the cooldown window per-Customer and receiver-side, with no exclusivity floor

The fixed seven-day Global Cooldown becomes a per-Customer Cooldown Window: an administrator-set
minimum age (default seven days, minimum one) that a Lead's latest Distribution Event must reach
before that Customer may draw it. The window belongs to the drawing Customer — a freshness tier
that composes with per-Customer Lead Rates — not to the Customer who received the Lead.

We deliberately set no floor above one day. That means no Customer has guaranteed exclusivity
anymore: a delivered Lead's effective protection equals the smallest window configured for any
other Customer. The uniform seven-day rule quietly promised every recipient a week; that promise
is intentionally withdrawn in favor of administrator control. The permanent rule is unchanged: a
Lead is never again eligible for the same Customer or Agency.

## Considered Options

Sender-side windows (the recipient's setting protects their own deliveries) were rejected: the
receiver-side reading matches how the operator actually used the legacy pipeline (a global
redistribution gate applied from the drawing side, manually shortened per run) and makes the
window a pricing lever rather than a protection setting.
