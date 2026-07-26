# Jawnix Lead Platform

Jawnix supplies customers with approved batches of leads by maintaining lead inventory and enforcing distribution rules. The active domain spans lead acquisition through batch delivery; billing and finance are outside the current product.

## Language

**Jawnix Platform**:
The active product that turns acquired lead data into approved lead batches delivered to customers. Billing, invoicing, payments, and financial reporting are legacy concerns outside this context.
_Avoid_: Lead tracker, lead-to-cash platform

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
The specific Google Maps niche or search query and geography that produced a Listing Observation. Lead quality is evaluated per Source Segment rather than for the Scraper as a whole.
_Avoid_: Scraper, lead inventory

**Source Cohort**:
Distribution Events grouped by Source Segment and original distribution period. Outcomes reported later are credited back to the cohort that delivered the Lead.
_Avoid_: Outcome-month cohort, scrape run

**Source Performance**:
Source Cohort metrics where Good and Poor rates use rated Leads, while Positive Response and Appointment Booked rates use all delivered Leads; every percentage includes its raw count. Quality ranks after 30 ratings and response or appointment performance ranks after 100 deliveries regardless of age, with smaller samples visible as Insufficient Data.
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
A Customer's quality report about a Lead received in a specific Distribution Event. It has one reason—invalid phone, wrong business or title, wrong state, duplicate received, do-not-contact or legal concern, or other—plus an optional note, and never changes eligibility by itself.
The report is immutable and closes as Dismissed, Corrected, or Suppressed with a required administrator resolution note.
_Avoid_: Lead suppression, CRM outcome

**Lead Outcome**:
An append-only customer-reported quality or commercial milestone attached to a specific Distribution Event and attributed to its Source Segment. Outcomes may be recorded at any later date, each milestone type counts at most once in metrics, corrections retain full history, and no outcome changes eligibility without an administrator action.
_Avoid_: Lead status, CRM record

**Customer Feedback**:
The minimal Customer-facing outcome set: Good, Poor, Positive Response, and Appointment Booked. Notes are optional, appointment date and time are required only when booked, and cancellation or no-show actions appear only for booked appointments.
_Avoid_: CRM workflow, appointment-held tracking

**Quality Rating**:
A Good or Poor Lead Outcome with an optional Customer note. Poor contributes to Source Segment metrics and may lead into a separate Lead Report, but never creates one automatically.
_Avoid_: Lead report, five-point score

**Positive Response**:
A Lead Outcome recorded when the prospect explicitly expresses interest or agrees to a follow-up. Mere connection, opening, automated reply, or neutral response does not qualify.
_Avoid_: Contact attempt, reply

**Appointment Booked**:
A Lead Outcome recorded only when a specific appointment date and time have been scheduled. Later cancellation or no-show is recorded as a separate outcome rather than changing the booking milestone.
_Avoid_: Scheduling intent, follow-up

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
