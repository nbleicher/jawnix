from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .activity import record_activity
from .models import (
    DistributionEvent,
    EligibilityHold,
    LeadDispositionTransition,
    LeadReport,
)


REPORT_DISPOSITIONS = {
    "invalid_phone": "invalid_phone",
    "wrong_business": "wrong_business_or_title",
    "do_not_contact": "do_not_contact_or_legal",
}
HOLD_DISPOSITIONS = {"invalid_phone", "do_not_contact"}


def apply_disposition_controls(
    session: Session,
    event: DistributionEvent,
    transition: LeadDispositionTransition,
) -> tuple[LeadReport | None, EligibilityHold | None]:
    """Materialize immutable report/hold controls for one disposition event."""
    report_reason = REPORT_DISPOSITIONS.get(transition.disposition)
    if report_reason is None:
        return None, None
    report = session.scalar(
        select(LeadReport).where(
            LeadReport.source_transition_id == transition.id
        )
    )
    if report is None:
        report = LeadReport(
            distribution_event_id=event.id,
            customer_id=transition.customer_id,
            source_transition_id=transition.id,
            reason=report_reason,
            details=transition.note,
            created_at=transition.created_at,
        )
        session.add(report)
        session.flush()
    if transition.disposition not in HOLD_DISPOSITIONS:
        return report, None
    hold = session.scalar(
        select(EligibilityHold).where(
            EligibilityHold.report_id == report.id
        )
    )
    if hold is None:
        hold = EligibilityHold(
            lead_id=event.lead_id,
            distribution_event_id=event.id,
            report_id=report.id,
            reason=transition.disposition,
            created_at=transition.created_at,
        )
        session.add(hold)
        session.flush()
        record_activity(
            session,
            action="eligibility_hold_applied",
            target_type="lead",
            target_id=event.lead_id,
            actor_id=transition.actor_user_id,
            reason=transition.note or transition.disposition,
            details={
                "holdId": str(hold.id),
                "reportId": str(report.id),
                "distributionEventId": event.id,
                "disposition": transition.disposition,
            },
        )
    return report, hold


def release_report_hold(
    session: Session,
    report: LeadReport,
    *,
    actor_id: str,
    reason: str,
) -> EligibilityHold | None:
    hold = session.scalar(
        select(EligibilityHold)
        .where(
            EligibilityHold.report_id == report.id,
            EligibilityHold.active.is_(True),
        )
        .with_for_update()
    )
    if hold is None:
        return None
    hold.active = False
    hold.released_by = actor_id
    hold.release_reason = reason
    hold.released_at = datetime.now(timezone.utc)
    record_activity(
        session,
        action="eligibility_hold_removed",
        target_type="lead",
        target_id=hold.lead_id,
        actor_id=actor_id,
        reason=reason,
        details={
            "holdId": str(hold.id),
            "reportId": str(report.id),
            "distributionEventId": hold.distribution_event_id,
        },
    )
    return hold
