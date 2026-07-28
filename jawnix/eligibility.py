"""Lead Report resolution and the eligibility controls that sit beside it.

A Lead Report is immutable evidence from a Customer about one Distribution
Event. Resolving it never edits it, and never edits the Distribution Event or
the Listing Observations underneath. Rewriting history so it agrees with a
decision is the failure this module exists to prevent: the report says what the
Customer said, the Distribution Event says what was delivered, and the controls
-- Eligibility Hold, Lead Correction, Lead Suppression -- are separate records
layered on top.

The three resolutions are three different decisions and are kept apart on
purpose:

* **Dismissed** judges the report unfounded. It releases the Eligibility Hold
  and changes nothing about the Lead.
* **Corrected** overrides the Lead's delivered title or state. It records the
  evidence it disagreed with, because a Current Listing can be superseded by a
  later Scrape Run and an override with nothing to compare against is an
  assertion rather than a correction.
* **Suppressed** makes the Lead ineligible for everyone. It does not change any
  delivered value, and restoring eligibility later returns the Lead to the
  ordinary rules rather than guaranteeing it will be allocated.

Only an administrator releases an Eligibility Hold. A Customer recording a
kinder disposition afterwards adds history; it does not reopen eligibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .feedback import release_report_hold
from .models import (
    Customer,
    DistributionEvent,
    EligibilityHold,
    Lead,
    LeadCorrectionEvent,
    LeadReport,
    LeadReportResolution,
    ListingObservation,
)


class ControlConflict(Exception):
    """An eligibility control was refused by a domain rule."""

    def __init__(self, message: str, *, guard: str) -> None:
        super().__init__(message)
        self.message = message
        self.guard = guard


#: Stated once, rendered everywhere. An Eligibility Hold exists precisely
#: because a Customer's own account of the Lead cannot lift it.
HOLD_RELEASE_RULE = (
    "Only an administrator resolving this Lead Report releases the "
    "Eligibility Hold. A later Customer disposition adds history and never "
    "restores eligibility."
)

#: The sentence every restore path must show. Eligibility is a precondition
#: for allocation, never a promise of it.
RESTORE_NOTICE = (
    "Restoring eligibility returns this Lead to the ordinary allocation "
    "rules -- Global Cooldown, permanent no-repeat history, and Licensed "
    "State scope still apply. It does not guarantee the Lead will be "
    "allocated."
)


@dataclass(frozen=True)
class LeadEvidence:
    """What a Lead's delivered title and state currently rest on."""

    kind: str
    label: str
    title: str
    state: str
    observation_id: int | None = None
    observed_at: datetime | None = None
    source: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "label": self.label,
            "title": self.title,
            "state": self.state,
            "observationId": self.observation_id,
            "observedAt": self.observed_at,
            "source": self.source,
        }


@dataclass(frozen=True)
class ControlAction:
    """One administrator decision, with the copy needed to confirm it."""

    name: str
    label: str
    consequence: str
    destructive: bool = False
    requires_reason: bool = True
    #: True when the action is meaningless without a proposed title or state.
    requires_override: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "label": self.label,
            "consequence": self.consequence,
            "destructive": self.destructive,
            "requiresReason": self.requires_reason,
            "requiresOverride": self.requires_override,
        }


DISMISS = ControlAction(
    name="dismiss",
    label="Dismiss report",
    consequence=(
        "Judges this report unfounded. The Eligibility Hold is released and "
        "the Lead returns to the ordinary allocation rules. Nothing about "
        "the Lead's delivered title or state changes."
    ),
)
CORRECT = ControlAction(
    name="correct",
    label="Correct the Lead",
    consequence=(
        "Overrides the Lead's delivered title or state and releases the "
        "Eligibility Hold. The override outranks the Current Listing until "
        "an administrator removes it; the Distribution Event that was "
        "already delivered is never rewritten."
    ),
    requires_override=True,
)
SUPPRESS = ControlAction(
    name="suppress",
    label="Suppress the Lead",
    consequence=(
        "Makes this Lead ineligible for every Customer until an "
        "administrator restores it. Its Listing Observations and "
        "Distribution Events are kept intact."
    ),
    destructive=True,
)

REPORT_ACTIONS: tuple[ControlAction, ...] = (DISMISS, CORRECT, SUPPRESS)

RESTORE_LEAD = ControlAction(
    name="restore",
    label="Restore eligibility",
    consequence=RESTORE_NOTICE,
)
REMOVE_CORRECTION = ControlAction(
    name="remove-correction",
    label="Remove correction",
    consequence=(
        "Drops the override so the Lead falls back to its Current Listing, "
        "or to its Legacy Listing Snapshot when it has none. Deliveries "
        "already made keep the values they were sent with."
    ),
    destructive=True,
)

_EVIDENCE_LABELS = {
    "current_listing": "Current Listing (Google Maps observation)",
    "legacy_snapshot": "Legacy Listing Snapshot (imported)",
    "prior_correction": "Active Lead Correction",
    "none": "No underlying listing evidence",
}


def lead_evidence(db: Session, lead: Lead) -> LeadEvidence:
    """What this Lead's delivered values currently rest on.

    Precedence mirrors the domain: an active Lead Correction outranks the
    Current Listing, which outranks the Legacy Listing Snapshot. A new
    correction is therefore judged against whichever of those it is about to
    displace.
    """
    if lead.active_correction_id is not None:
        active = db.get(LeadCorrectionEvent, lead.active_correction_id)
        if active is not None:
            return LeadEvidence(
                kind="prior_correction",
                label=_EVIDENCE_LABELS["prior_correction"],
                title=active.title,
                state=active.state,
                observed_at=active.created_at,
                source=active.reason,
            )
    if lead.current_listing_observation_id is not None:
        observation = db.get(
            ListingObservation,
            lead.current_listing_observation_id,
        )
        if observation is not None:
            return LeadEvidence(
                kind="current_listing",
                label=_EVIDENCE_LABELS["current_listing"],
                title=observation.title,
                state=observation.state,
                observation_id=observation.id,
                observed_at=observation.observed_at,
                source=observation.source,
            )
    if lead.legacy_title or lead.legacy_state:
        return LeadEvidence(
            kind="legacy_snapshot",
            label=_EVIDENCE_LABELS["legacy_snapshot"],
            title=lead.legacy_title,
            state=lead.legacy_state,
            source=lead.source_flow,
        )
    return LeadEvidence(
        kind="none",
        label=_EVIDENCE_LABELS["none"],
        title="",
        state="",
    )


def record_correction(
    db: Session,
    lead: Lead,
    *,
    actor_id: object,
    reason: str,
    title: str | None,
    state: str | None,
) -> LeadCorrectionEvent:
    """Apply an override to ``lead``, grounded in what it displaces.

    Does not commit. Raises :class:`ControlConflict` when the override would
    change nothing, so an empty correction never enters the audit trail as if
    it were a decision.
    """
    evidence = lead_evidence(db, lead)
    proposed_title = title.strip() if title is not None else lead.title
    proposed_state = state or lead.state
    if proposed_title == lead.title and proposed_state == lead.state:
        raise ControlConflict(
            "A Lead Correction must change the delivered title or state.",
            guard="empty_correction",
        )
    correction = LeadCorrectionEvent(
        lead_id=lead.id,
        action="applied",
        title=proposed_title,
        state=proposed_state,
        actor_id=str(actor_id),
        reason=reason.strip(),
        supersedes_correction_id=lead.active_correction_id,
        based_on_kind=evidence.kind,
        based_on_observation_id=evidence.observation_id,
        based_on_title=evidence.title,
        based_on_state=evidence.state,
    )
    db.add(correction)
    db.flush()
    lead.active_correction_id = correction.id
    lead.title = correction.title
    lead.state = correction.state
    return correction


def _resolve(
    db: Session,
    report: LeadReport,
    *,
    action: str,
    actor_id: object,
    note: str,
) -> tuple[LeadReportResolution, EligibilityHold | None, Lead]:
    """Close one open report. Shared plumbing only -- effects stay apart."""
    if report.status != "open":
        raise ControlConflict(
            "This Lead Report is already resolved.",
            guard="already_resolved",
        )
    event = db.get(DistributionEvent, report.distribution_event_id)
    lead = db.scalar(
        select(Lead).where(Lead.id == event.lead_id).with_for_update()
    )
    resolution = LeadReportResolution(
        report_id=report.id,
        action=action,
        note=note.strip(),
        actor_id=str(actor_id),
    )
    db.add(resolution)
    hold = release_report_hold(
        db,
        report,
        actor_id=str(actor_id),
        reason=note.strip(),
    )
    # The report itself is never edited. Its status is the outcome of the
    # separate resolution record, not a rewrite of what the Customer said.
    report.status = action
    return resolution, hold, lead


def dismiss_report(
    db: Session,
    report: LeadReport,
    *,
    actor_id: object,
    note: str,
) -> LeadReportResolution:
    """Judge the report unfounded. Releases the hold, touches nothing else."""
    resolution, _, _ = _resolve(
        db,
        report,
        action="dismissed",
        actor_id=actor_id,
        note=note,
    )
    return resolution


def correct_from_report(
    db: Session,
    report: LeadReport,
    *,
    actor_id: object,
    note: str,
    title: str | None,
    state: str | None,
) -> LeadCorrectionEvent:
    """Override the Lead's delivered values in answer to the report."""
    if title is None and state is None:
        raise ControlConflict(
            "A Lead Correction must propose a title, a state, or both.",
            guard="missing_override",
        )
    _, _, lead = _resolve(
        db,
        report,
        action="corrected",
        actor_id=actor_id,
        note=note,
    )
    return record_correction(
        db,
        lead,
        actor_id=actor_id,
        reason=note,
        title=title,
        state=state,
    )


def suppress_from_report(
    db: Session,
    report: LeadReport,
    *,
    actor_id: object,
    note: str,
) -> Lead:
    """Convert the report into audited Lead Suppression."""
    _, _, lead = _resolve(
        db,
        report,
        action="suppressed",
        actor_id=actor_id,
        note=note,
    )
    lead.suppressed = True
    lead.suppression_reason = note.strip()
    return lead


def describe_report(db: Session, report: LeadReport) -> dict[str, object]:
    """One Lead Report with its evidence and current control state."""
    event = db.get(DistributionEvent, report.distribution_event_id)
    lead = db.get(Lead, event.lead_id) if event is not None else None
    customer = db.get(Customer, report.customer_id)
    hold = db.scalar(
        select(EligibilityHold).where(EligibilityHold.report_id == report.id)
    )
    resolution = db.scalar(
        select(LeadReportResolution).where(
            LeadReportResolution.report_id == report.id
        )
    )
    active_correction = (
        db.get(LeadCorrectionEvent, lead.active_correction_id)
        if lead is not None and lead.active_correction_id is not None
        else None
    )
    evidence = (
        lead_evidence(db, lead)
        if lead is not None
        else LeadEvidence(kind="none", label="", title="", state="")
    )
    return {
        "id": str(report.id),
        "reason": report.reason,
        "reasonLabel": _REPORT_REASONS.get(report.reason, report.reason),
        # The Customer's words, verbatim and never edited by a resolution.
        "details": report.details,
        "status": report.status,
        "createdAt": report.created_at,
        "href": f"/app/admin/fulfillment/reports/{report.id}",
        "customer": {
            "id": report.customer_id,
            "name": customer.name if customer else "",
            "href": (
                f"/app/admin/customers/{report.customer_id}"
                if customer
                else ""
            ),
        },
        "distributionEvent": _describe_event(event),
        "lead": _describe_lead(lead, active_correction),
        "evidence": evidence.as_dict(),
        "controls": {
            "eligibilityHeld": hold is not None and hold.active,
            "holdId": str(hold.id) if hold is not None else None,
            "holdReason": hold.reason if hold is not None else "",
            "holdReleasedAt": hold.released_at if hold is not None else None,
            "holdRelease": HOLD_RELEASE_RULE,
            # Stated as data so no surface has to remember the rule.
            "holdReleasableByCustomer": False,
            "suppressed": bool(lead.suppressed) if lead is not None else False,
            "suppressionReason": (
                lead.suppression_reason if lead is not None else ""
            ),
            "restoreNotice": RESTORE_NOTICE,
            # Undoing a suppression belongs on the screen that made it, so an
            # administrator is not sent hunting for the Lead elsewhere.
            "actions": (
                [RESTORE_LEAD.as_dict()]
                if lead is not None and lead.suppressed
                else []
            ),
        },
        "resolution": (
            {
                "action": resolution.action,
                "note": resolution.note,
                "actorId": resolution.actor_id,
                "createdAt": resolution.created_at,
            }
            if resolution is not None
            else None
        ),
        "actions": [
            action.as_dict()
            for action in (REPORT_ACTIONS if report.status == "open" else ())
        ],
    }


def report_summaries(
    db: Session,
    *,
    limit: int = 50,
) -> list[dict[str, object]]:
    """Open Lead Reports as list rows, newest first.

    Deliberately not :func:`describe_report`: a queue needs enough to choose
    what to open, and building the full evidence-and-controls aggregate per
    row turned one workspace load into a query per report.
    """
    reports = list(
        db.scalars(
            select(LeadReport)
            .where(LeadReport.status == "open")
            .order_by(LeadReport.created_at.desc(), LeadReport.id)
            .limit(limit)
        )
    )
    if not reports:
        return []
    customers = {
        customer.id: customer
        for customer in db.scalars(
            select(Customer).where(
                Customer.id.in_({item.customer_id for item in reports})
            )
        )
    }
    held = set(
        db.scalars(
            select(EligibilityHold.report_id).where(
                EligibilityHold.report_id.in_({item.id for item in reports}),
                EligibilityHold.active.is_(True),
            )
        )
    )
    return [
        {
            "id": str(item.id),
            "reason": item.reason,
            "reasonLabel": _REPORT_REASONS.get(item.reason, item.reason),
            "details": item.details,
            "status": item.status,
            "createdAt": item.created_at,
            "href": f"/app/admin/fulfillment/reports/{item.id}",
            "customer": {
                "id": item.customer_id,
                "name": (
                    customers[item.customer_id].name
                    if item.customer_id in customers
                    else ""
                ),
                "href": f"/app/admin/customers/{item.customer_id}",
            },
            "eligibilityHeld": item.id in held,
        }
        for item in reports
    ]


def active_holds(db: Session, *, limit: int = 50) -> list[dict[str, object]]:
    """Eligibility Holds currently blocking allocation, newest first."""
    holds = db.scalars(
        select(EligibilityHold)
        .where(EligibilityHold.active.is_(True))
        .order_by(EligibilityHold.created_at.desc(), EligibilityHold.id)
        .limit(limit)
    )
    described = []
    for hold in holds:
        report = db.get(LeadReport, hold.report_id)
        lead = db.get(Lead, hold.lead_id)
        described.append(
            {
                "id": str(hold.id),
                "leadId": hold.lead_id,
                "leadPhone": lead.phone if lead else "",
                "reason": hold.reason,
                "reasonLabel": _HOLD_REASONS.get(hold.reason, hold.reason),
                "createdAt": hold.created_at,
                "reportId": str(hold.report_id),
                "reportStatus": report.status if report else "",
                "href": f"/app/admin/fulfillment/reports/{hold.report_id}",
                "release": HOLD_RELEASE_RULE,
            }
        )
    return described


def suppressed_leads(db: Session, *, limit: int = 50) -> list[dict[str, object]]:
    """Leads an administrator has made ineligible, newest first."""
    return [
        {
            "id": lead.id,
            "phone": lead.phone,
            "title": lead.title,
            "state": lead.state,
            "reason": lead.suppression_reason,
            "restoreNotice": RESTORE_NOTICE,
            "actions": [RESTORE_LEAD.as_dict()],
        }
        for lead in db.scalars(
            select(Lead)
            .where(Lead.suppressed.is_(True))
            .order_by(Lead.id.desc())
            .limit(limit)
        )
    ]


_REPORT_REASONS = {
    "invalid_phone": "Invalid phone",
    "wrong_business_or_title": "Wrong business or title",
    "wrong_state": "Wrong state",
    "duplicate": "Duplicate received",
    "do_not_contact_or_legal": "Do not contact or legal concern",
    "other": "Other",
}

_HOLD_REASONS = {
    "invalid_phone": "Invalid phone",
    "do_not_contact": "Do not contact",
}


def _describe_event(event: DistributionEvent | None) -> dict[str, object]:
    """The delivered record, exactly as it was committed."""
    if event is None:
        return {}
    return {
        "id": event.id,
        "phone": event.phone,
        "title": event.title,
        "state": event.state,
        "customerName": event.customer_name,
        "agencyName": event.agency_name,
        "deliveredAt": event.delivered_at,
        "listingProvenance": event.listing_provenance,
        "requestId": str(event.request_id) if event.request_id else None,
    }


def _describe_lead(
    lead: Lead | None,
    active_correction: LeadCorrectionEvent | None,
) -> dict[str, object]:
    if lead is None:
        return {}
    return {
        "id": lead.id,
        "phone": lead.phone,
        "title": lead.title,
        "state": lead.state,
        "suppressed": lead.suppressed,
        "correction": (
            {
                "id": str(active_correction.id),
                "title": active_correction.title,
                "state": active_correction.state,
                "reason": active_correction.reason,
                "createdAt": active_correction.created_at,
                "basedOnKind": active_correction.based_on_kind,
                "basedOnLabel": _EVIDENCE_LABELS.get(
                    active_correction.based_on_kind,
                    "Evidence not recorded",
                ),
                "basedOnTitle": active_correction.based_on_title,
                "basedOnState": active_correction.based_on_state,
                "actions": [REMOVE_CORRECTION.as_dict()],
            }
            if active_correction is not None
            else None
        ),
    }


__all__ = [
    "ControlAction",
    "ControlConflict",
    "HOLD_RELEASE_RULE",
    "LeadEvidence",
    "REMOVE_CORRECTION",
    "REPORT_ACTIONS",
    "RESTORE_LEAD",
    "RESTORE_NOTICE",
    "active_holds",
    "correct_from_report",
    "describe_report",
    "dismiss_report",
    "lead_evidence",
    "record_correction",
    "report_summaries",
    "suppress_from_report",
    "suppressed_leads",
]
