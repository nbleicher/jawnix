# Price Clean inventory separately from the Customer's Lead Rate

Clean (DB3) requests pay a single platform rate — the Clean Rate, configured
by `JAWNIX_CLEAN_LEAD_RATE_CENTS_PER_THOUSAND` — instead of the Customer's own
Lead Rate. Standard requests keep paying the Customer's rate, whichever of DB1
or DB2 they draw from.

This replaces ADR 0018's rule that billing belongs to the Customer, not the
Lead Database, for Clean only. The reasoning inverts: DB1 and DB2 differ in
source, not in what the Customer must do with the rows, so one rate covers
both. DB3 rows are already scrubbed, which is worth a different price to every
buyer regardless of their negotiated rate.

While the Clean Rate is unset, Clean is off: no Clean request is accepted and
the Standard-or-Clean choice stays hidden. Every Clean buyer is billed; a
Clean request from an unbilled Customer is refused. Both rates freeze on the
request at submission, so a later price change never reprices in-flight work.
