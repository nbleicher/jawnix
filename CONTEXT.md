# Jawnix Lead Platform

Jawnix supplies customers with approved batches of leads by maintaining lead inventory and enforcing distribution rules. The active domain spans lead acquisition through batch delivery; billing and finance are outside the current product.

## Language

**Jawnix Platform**:
The active product that turns acquired lead data into approved lead batches delivered to customers. Billing, invoicing, payments, and financial reporting are legacy concerns outside this context.
_Avoid_: Lead tracker, lead-to-cash platform

**Administrator Assurance**:
The strength of the identity verification completed for an Administrator Session; administration requires a password plus a currently verified Authenticator Factor.
_Avoid_: Login status, trusted browser

**Authenticator Factor**:
An authenticator-app credential held by an administrator and verified during enrollment before it can establish Administrator Assurance.
_Avoid_: Verification code, MFA secret

**Primary Authenticator**:
The Authenticator Factor an administrator normally uses to establish Administrator Assurance.
_Avoid_: Only factor, default password

**Backup Authenticator**:
A second verified Authenticator Factor stored separately from the Primary Authenticator and used when the primary is unavailable.
_Avoid_: Recovery code, duplicate authenticator

**Break-glass Recovery**:
A two-person, explicitly authorized operator procedure that revokes Administrator Sessions and restores access only to Authenticator Factor enrollment after both factors are lost.
_Avoid_: Password reset, self-service recovery, administrator bypass

**Scraper**:
The Google Maps acquisition setup that collects candidate leads for Jawnix.
_Avoid_: NPPES collector, inventory synchronization

**Scrape Run**:
A staged execution of the Scraper under one Scraper Configuration. Its candidate data becomes visible only after validation and atomic commit; failure leaves the last successful Scraper Dataset unchanged.
_Avoid_: Inventory sync, nightly sync

**Scrape Anomaly**:
A completed staged Scrape Run flagged when a Source Segment has zero valid listings or differs from the median of its last seven successful runs by more than 50% down or 200% up; a new segment flags only at zero. Thresholds belong to Scraper Configuration, and flagged output requires administrator confirmation before commit.
_Avoid_: Failed run, source recommendation

**Nightly Review**:
A durable internal summary covering Scraper Configuration and run status, per-segment acquisition counts, Inventory Sync and inventory totals, waiting requests and conflicts, recommendations, and failures. Telegram receives one concise linked summary with Confirm or Deny actions for held anomalies.
_Avoid_: Transient alert, scrape log

**Scraper Configuration**:
An immutable version of the Source Segments and acquisition parameters used by the Scraper; every Scrape Run references one, and activation or rollback selects a version without rewriting history. An approved version activates for the next nightly run, while an immediate run requires a separate administrator action.
_Avoid_: Source recommendation, scrape run

**Inventory Sync**:
The atomic ingestion of a specific committed Scraper Dataset version into Lead Inventory, with validation, deduplication, and source provenance preserved. It may run alongside allocations, which see only committed inventory; failure rolls back every change and retries the same version later.
_Avoid_: Scrape run, scraper

**Scraper Dataset**:
The Scraper's durable record of acquired candidate leads and the replay source for Inventory Sync. It is not the authority for allocation or distribution history.
_Avoid_: Lead inventory, distribution database

**Source Segment**:
An explicit, versioned Google Maps keyword-and-state pair with stable identity, Niche confirmation state, cadence, and Active, Reduced, or Paused status. Lead quality is evaluated per Source Segment rather than for the Scraper as a whole; changing one pair never changes the same keyword in another state.
_Avoid_: Scraper, lead inventory

**Source Cohort**:
Distribution Events grouped by Source Segment and original distribution period. Outcomes reported later are credited back to the cohort that delivered the Lead.
_Avoid_: Outcome-month cohort, scrape run

**Source Performance**:
Immutable daily Source Cohort metrics where Good and Poor rates use Quality Ratings, while disposition rates—including Positive Response and Appointment Booked—use Worked Leads; every percentage includes its raw count. Prescriptive analysis uses the trailing 90-day cohort and requires 30 Quality Ratings, 100 Worked Leads, a confirmed Niche, and two eligible same-Niche/same-state peers. Smaller samples and all-time results remain visible but non-prescriptive.
_Avoid_: Raw outcome count, unrated quality rate

**Source Recommendation**:
An evidence-based proposal to expand, reduce, or pause a Source Segment based on comparisons within the same niche. Cross-niche metrics remain visible, and no recommendation changes Scraper behavior without explicit administrator approval.
_Avoid_: Automatic scraper optimization, source ranking

**Lead Inventory**:
Jawnix's authoritative set of validated, deduplicated leads available for allocation.
_Avoid_: Scraper dataset, leads database

**Legacy Inventory**:
Previously acquired Leads, including existing NPPES-derived Leads, that remain eligible with provenance preserved even though their acquisition source is no longer active. Their outcomes remain visible overall but do not affect Google Maps Source Segment rankings or recommendations.
_Avoid_: Suppressed leads, active source

**Legacy Listing Snapshot**:
The imported title and state used for an eligible Legacy Inventory Lead that has no Current Listing. It remains historical evidence after a Current Listing or Lead Correction becomes authoritative for future deliveries.
_Avoid_: Current listing, active source observation

**Lead**:
A unique callable endpoint identified by its normalized phone number. Listing changes and apparent phone reassignment do not create a new Lead or reset its distribution history.
_Avoid_: Business, listing, contact row

**Listing Observation**:
A Google Maps business listing captured by the Scraper at a particular time. Source statuses such as "permanently closed" are preserved as evidence but do not determine Lead eligibility.
_Avoid_: Lead, inventory row

**Current Listing**:
The most recently observed Valid Listing for a Lead and the default source of its delivered title and state. A Lead Correction overrides it, while a Legacy Listing Snapshot is used only when no Current Listing exists.
_Avoid_: Lead, newest row

**Valid Listing**:
A Listing Observation with a normalizable US phone number, a non-empty deliverable title, and a valid Google Maps business-location state. A newer invalid observation cannot replace the Current Listing.
_Avoid_: Newest listing, phone-area-derived listing

**Customer**:
The durable party that requests and receives lead batches and owns permanent no-repeat history.
_Avoid_: Agent, recipient identity

**Deactivated Customer**:
A Customer blocked from login and new Batch Requests but retained in history. True deletion is available only before any request, distribution, or outcome history exists.
_Avoid_: Deleted customer, suspended request

**Customer Tombstone**:
An anonymous Customer identity retained after personal data erasure when historical records exist. It preserves immutable audit and no-repeat history without retaining login or profile details.
_Avoid_: Active customer, hard deletion

**Licensed States**:
The Customer-maintained set of states where the Customer is authorized to operate. Removing a state automatically narrows unallocated requests with notification, while additions apply only to future requests and historical distributions never change.
_Avoid_: Preferred states, administrator-assigned states

**User Account**:
A replaceable authentication identity used to access Jawnix on behalf of a Customer. Each Customer has one active User Account, and replacing it never creates a new Customer or resets distribution history.
_Avoid_: Customer, agent

**Agency**:
A group of Customers that share permanent no-repeat history.
_Avoid_: Customer, account

**Deactivated Agency**:
An Agency removed from active use but retained for historical attribution. True deletion is available only when it has no Customers, Batch Requests, Distribution Events, or Lead Outcomes; otherwise its tombstone remains permanent.
_Avoid_: Deleted agency, active agency

**Distribution Event**:
An immutable record created when a Lead's allocation and batch generation commit, snapshotting the Customer, Agency, delivered phone, title, state, and the Listing Observation or Lead Correction used. Later account, Agency, inventory, or delivery-status changes never rewrite the event.
_Avoid_: Current customer membership, email delivery

**Global Cooldown**:
The seven-day period after a Lead's latest Distribution Event during which it is ineligible for every other Customer. Afterward it may become eligible for unrelated Customers but never again for the same Customer or Agency.
_Avoid_: Permanent no-repeat, customer cooldown

**Lead Suppression**:
A reversible internal state that makes a Lead ineligible without deleting its Listing Observations or Distribution Events. Only an administrator may change it, with a required reason and immutable audit entry; removing it restores normal eligibility rules rather than guaranteeing allocation.
_Avoid_: Lead deletion, quarantine

**Lead Report**:
A Customer's immutable quality report about a Lead received in a specific Distribution Event. It has one reason—invalid phone, wrong business or title, wrong state, duplicate received, do-not-contact or legal concern, or other—plus an optional note. Invalid Phone, Wrong Business, and Do Not Contact dispositions create a non-duplicated matching report automatically.
The report is immutable and closes as Dismissed, Corrected, or Suppressed with a required administrator resolution note.
_Avoid_: Lead suppression, CRM outcome

**Eligibility Hold**:
A reversible allocation block created automatically for an Invalid Phone or Do Not Contact disposition and tied to its Lead Report. Customer corrections cannot release it. Administrator dismissal or correction releases it; a Suppressed resolution releases it while converting the Lead to audited Lead Suppression. Wrong Business is report-only and never creates a hold.
_Avoid_: Lead Suppression, customer-released hold

**Lead Outcome**:
A legacy-compatible append-only Customer feedback record attached to a specific Distribution Event and attributed to its Source Segment. Good and Poor records supply Quality Rating history; the legacy write contract and historical commercial milestones remain available until their idempotent migration into Lead Disposition history. New Customer commercial feedback uses Lead Dispositions. Corrections retain full history, and no outcome changes eligibility without an administrator action.
_Avoid_: Lead status, CRM record

**Customer Feedback**:
The single-phone Customer workflow for finding the Customer's most recent Distribution Event for that delivered phone, confirming the business, phone, delivery date, and Batch, then recording one Lead Disposition and an optional independent Quality Rating. A failed lookup always returns the same response whether the phone is invalid, absent, or belongs to another Customer, and the workflow does not support bulk entry.
_Avoid_: Inventory search, bulk feedback, CRM workflow

**Lead Disposition**:
The Customer's append-only status history for one Distribution Event. Controlled values are No Contact, Not Interested, Positive Response, Appointment Booked, Appointment Canceled, Appointment No-show, Invalid Phone, Wrong Business, Do Not Contact, and Other; Other requires a note. Every change retains its predecessor while the latest transition is materialized as the current disposition. Invalid Phone, Wrong Business, and Do Not Contact also materialize the matching Lead Report controls defined above.
_Avoid_: Mutable lead status, Lead Report, Lead Suppression

**Quality Rating**:
A Good or Poor Lead Outcome with an optional Customer note. Poor contributes to Source Segment metrics and may lead into a separate Lead Report, but never creates one automatically.
_Avoid_: Lead report, five-point score

**Positive Response**:
A Lead Disposition recorded when the prospect explicitly expresses interest or agrees to a follow-up. Mere connection, opening, automated reply, or neutral response does not qualify.
_Avoid_: Contact attempt, reply

**Appointment Booked**:
A Lead Disposition recorded when a specific appointment has been scheduled; date and time are not required in Jawnix. Later cancellation or no-show is recorded as a separate transition and does not erase the historical booking milestone.
_Avoid_: Scheduling intent, appointment-held tracking

**Lead Correction**:
A reversible, audited administrator override of a Lead's delivered title or state. It remains authoritative until explicitly removed; conflicting newer Listing Observations are flagged for review rather than applied automatically.
_Avoid_: Source edit, listing deletion

**Batch Request**:
A Customer's request for an exact quantity of 1 to 100,000 eligible Leads within a specified Licensed State scope. A Customer may have multiple active requests, ordered by approval time within that Customer's queue.
_Avoid_: Partial order, batch artifact

**Request Approval**:
An explicit operator authorization for a Batch Request's first allocation attempt. It survives automatic removal of unlicensed states, but any Customer-requested scope change requires a new request and losing every requested state cancels it.
_Avoid_: Inventory conflict decision, delivery approval

**Waiting for Inventory**:
The state of an approved Batch Request whose full quantity is not currently eligible. Nothing is allocated or generated while waiting, and every successful Inventory Sync re-evaluates waiting requests through Fulfillment Rotation.
_Avoid_: Partial fulfillment, failed request

**Fulfillment Rotation**:
Agency-level round-robin ordered by least recent fulfillment, with standalone Customers treated as one-Customer Agencies. Each Agency turn selects its least-recently-fulfilled Customer and oldest approved request, fulfills at most one, and then advances without bypassing Inventory Conflict rules.
_Avoid_: Global request FIFO, account-weighted rotation

**Canceled Request**:
A Batch Request withdrawn before any Distribution Event commits. Cancellation is terminal and cannot release Leads from an already generated batch.
_Avoid_: Voided batch, delivery failure

**Batch Artifact**:
The exact CSV materialization of a fulfilled Batch Request. Its file expires after 30 days while its internal history remains permanent; an administrator may regenerate the exact file through an audited action, starting a new 30-day retention period.
_Avoid_: Batch request, distribution history

**Inventory Conflict**:
A situation where an older Batch Request cannot be fulfilled but a newer request could consume Leads eligible for both requests. One pending operator decision may authorize one attempt against the current inventory snapshot; denial or silence keeps the newer request waiting, and the conflict may recur only after a material change.
_Avoid_: Automatic queue bypass, inventory shortage
