# Keep Lead Report controls beside the report

Resolving a Lead Report never edits the report, the Distribution Event it concerns, or the Listing Observations underneath it. The report records what a Customer said, the Distribution Event records what was delivered, and Eligibility Hold, Lead Correction, and Lead Suppression are separate records layered on top. Rewriting history so it agrees with a decision would destroy the evidence the decision was made from.

Dismissed, Corrected, and Suppressed are three decisions with three effects and three endpoints, not one resolution carrying an action name. Dismissal judges the report unfounded and releases the Eligibility Hold. Correction overrides the Lead's delivered title or state. Suppression makes the Lead ineligible for every Customer. Each states its own consequence before it is taken, and correcting and suppressing are additionally recorded against the Lead so its own history explains the change.

A Lead Correction records the evidence it overrode — which of the Current Listing, Legacy Listing Snapshot, or prior correction it displaced, and what that said. A Current Listing can be superseded by a later Scrape Run, so an override with nothing left to compare against is an assertion rather than a correction.

Only an administrator releases an Eligibility Hold. A Customer recording a kinder disposition afterwards adds history and never restores eligibility. Restoring eligibility, whether by releasing a hold or removing a suppression, returns the Lead to Global Cooldown, permanent no-repeat history, and Licensed State scope; it is never a promise that the Lead will be allocated.
