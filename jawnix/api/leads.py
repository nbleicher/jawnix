from __future__ import annotations

import uuid
from fastapi import Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..activity import record_activity
from ..auth import Principal, require_admin
from ..config import Settings, get_settings
from ..database import get_db
from ..eligibility import (
    RESTORE_NOTICE,
    ControlConflict,
    correct_from_report,
    describe_report,
    dismiss_report,
    lead_evidence,
    record_correction,
    suppress_from_report,
)
from ..inventory import suppressed_leads_page
from ..lead_lookup import lead_lookup
from ..models import LeadReport, Lead, LeadCorrectionEvent
from ..schemas import (
    ActionReason,
    LeadCorrectionApply,
    LeadReportCorrect,
    LeadReportNote,
)
from fastapi import APIRouter


router = APIRouter()


@router.get("/api/admin/suppressed-leads")
def admin_suppressed_leads(
    page: int = Query(default=1, ge=1),
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return suppressed_leads_page(db, page=page)


@router.get("/api/admin/leads/lookup")
def admin_lead_lookup(
    phone: str | None = Query(default=None, max_length=40),
    lead_id: int | None = Query(default=None, alias="lead", ge=1),
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Everything about one phone number (or Lead id, from Activity links)."""
    if phone is None and lead_id is None:
        raise HTTPException(status_code=422, detail="Enter a phone number.")
    return lead_lookup(db, settings, phone=phone, lead_id=lead_id)


def _evidence_payload(correction: LeadCorrectionEvent) -> dict:
    """What the override disagreed with, as the correction row recorded it."""
    return {
        "kind": correction.based_on_kind,
        "title": correction.based_on_title,
        "state": correction.based_on_state,
        "observationId": correction.based_on_observation_id,
    }


def _correction_activity(
    db: Session,
    correction: LeadCorrectionEvent,
    *,
    principal: Principal,
    reason: str,
    origin: dict | None = None,
) -> None:
    record_activity(
        db,
        action="lead_correction_applied",
        target_type="lead",
        target_id=correction.lead_id,
        actor_id=principal.user_id,
        reason=reason,
        details={
            "before": {
                "correctionId": (
                    str(correction.supersedes_correction_id)
                    if correction.supersedes_correction_id
                    else None
                ),
                "title": correction.based_on_title,
                "state": correction.based_on_state,
            },
            "after": {
                "correctionId": str(correction.id),
                "title": correction.title,
                "state": correction.state,
            },
            # The evidence travels with the decision, so a later Scrape Run
            # superseding the Current Listing cannot strand this override
            # with nothing to judge it against.
            "evidence": _evidence_payload(correction),
            **(origin or {}),
        },
    )


@router.put("/api/admin/leads/{lead_id}/suppression")
def suppress_lead(
    lead_id: int,
    payload: ActionReason,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Make a Lead ineligible for every Customer, with a recorded reason."""
    lead = db.scalar(
        select(Lead).where(Lead.id == lead_id).with_for_update()
    )
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead was not found.")
    if not lead.suppressed:
        lead.suppressed = True
        lead.suppression_reason = payload.reason.strip()
        record_activity(
            db,
            action="lead_suppressed",
            target_type="lead",
            target_id=lead.id,
            actor_id=principal.user_id,
            reason=payload.reason,
            details={
                "before": {"suppressed": False},
                "after": {"suppressed": True},
            },
        )
    db.commit()
    return {
        "leadId": lead.id,
        "suppressed": lead.suppressed,
        "reason": lead.suppression_reason,
        "restoreNotice": RESTORE_NOTICE,
    }


@router.delete("/api/admin/leads/{lead_id}/suppression")
def unsuppress_lead(
    lead_id: int,
    payload: ActionReason,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Return a Lead to the ordinary allocation rules, with a recorded reason.

    Restoring eligibility is not an allocation: Global Cooldown, permanent
    no-repeat history, and Licensed State scope all still apply afterwards.
    ``restoreNotice`` says so wherever this is surfaced.
    """
    lead = db.scalar(
        select(Lead).where(Lead.id == lead_id).with_for_update()
    )
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead was not found.")
    if lead.suppressed:
        previous_reason = lead.suppression_reason
        lead.suppressed = False
        lead.suppression_reason = ""
        record_activity(
            db,
            action="lead_unsuppressed",
            target_type="lead",
            target_id=lead.id,
            actor_id=principal.user_id,
            reason=payload.reason,
            details={
                "before": {
                    "suppressed": True,
                    "suppressionReason": previous_reason,
                },
                "after": {"suppressed": False},
            },
        )
    db.commit()
    return {
        "leadId": lead.id,
        "suppressed": lead.suppressed,
        "reason": lead.suppression_reason,
        "restoreNotice": RESTORE_NOTICE,
    }


@router.put("/api/admin/leads/{lead_id}/correction")
def apply_lead_correction(
    lead_id: int,
    payload: LeadCorrectionApply,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Override a Lead's delivered title or state against its evidence."""
    lead = db.scalar(
        select(Lead).where(Lead.id == lead_id).with_for_update()
    )
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead was not found.")
    try:
        correction = record_correction(
            db,
            lead,
            actor_id=principal.user_id,
            reason=payload.reason,
            title=payload.title,
            state=payload.state,
        )
    except ControlConflict as conflict:
        db.rollback()
        raise HTTPException(status_code=422, detail=conflict.message) from None
    _correction_activity(
        db,
        correction,
        principal=principal,
        reason=payload.reason,
    )
    db.commit()
    return {
        "leadId": lead.id,
        "correctionId": str(correction.id),
        "title": lead.title,
        "state": lead.state,
        "evidence": _evidence_payload(correction),
    }


@router.delete("/api/admin/leads/{lead_id}/correction")
def remove_lead_correction(
    lead_id: int,
    payload: ActionReason,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Drop the override so the Lead falls back to the evidence underneath."""
    lead = db.scalar(
        select(Lead).where(Lead.id == lead_id).with_for_update()
    )
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead was not found.")
    if lead.active_correction_id is None:
        raise HTTPException(
            status_code=409,
            detail="Lead has no active correction.",
        )
    active = db.get(LeadCorrectionEvent, lead.active_correction_id)
    previous = {
        "correctionId": str(active.id),
        "title": lead.title,
        "state": lead.state,
    }
    # Resolve the fallback *after* clearing the override, so the evidence
    # reported is what the Lead actually reverts to rather than the override
    # that is going away. Restore the override if there is nowhere to land.
    lead.active_correction_id = None
    evidence = lead_evidence(db, lead)
    if evidence.kind == "none":
        lead.active_correction_id = active.id
        raise HTTPException(
            status_code=409,
            detail=(
                "This Lead Correction cannot be removed because there is no "
                "Current Listing or Legacy Listing Snapshot to fall back to."
            ),
        )
    removal = LeadCorrectionEvent(
        lead_id=lead.id,
        action="removed",
        title=active.title,
        state=active.state,
        actor_id=str(principal.user_id),
        reason=payload.reason.strip(),
        supersedes_correction_id=active.id,
        based_on_kind=evidence.kind,
        based_on_observation_id=evidence.observation_id,
        based_on_title=evidence.title,
        based_on_state=evidence.state,
    )
    db.add(removal)
    lead.title = evidence.title
    lead.state = evidence.state
    record_activity(
        db,
        action="lead_correction_removed",
        target_type="lead",
        target_id=lead.id,
        actor_id=principal.user_id,
        reason=payload.reason,
        details={
            "before": previous,
            "after": {
                "correctionId": None,
                "title": lead.title,
                "state": lead.state,
            },
            "evidence": _evidence_payload(removal),
        },
    )
    db.commit()
    return {
        "leadId": lead.id,
        "correctionId": None,
        "title": lead.title,
        "state": lead.state,
        "evidence": _evidence_payload(removal),
    }


@router.get("/api/admin/lead-reports/{report_id}")
def admin_lead_report_detail(
    report_id: uuid.UUID,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """One Lead Report, its evidence, and the controls sitting beside it."""
    report = db.get(LeadReport, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Lead Report was not found.")
    return describe_report(db, report)


def _open_report(db: Session, report_id: uuid.UUID) -> LeadReport:
    report = db.scalar(
        select(LeadReport)
        .where(LeadReport.id == report_id)
        .with_for_update()
    )
    if report is None:
        raise HTTPException(status_code=404, detail="Lead Report was not found.")
    return report


def _report_activity(
    db: Session,
    report: LeadReport,
    *,
    action: str,
    principal: Principal,
    note: str,
    after: dict,
) -> None:
    record_activity(
        db,
        action=f"lead_report_{action}",
        target_type="lead_report",
        target_id=report.id,
        actor_id=principal.user_id,
        reason=note,
        details={
            "before": {"status": "open"},
            "after": {"status": report.status, **after},
            "distributionEventId": report.distribution_event_id,
            "reportReason": report.reason,
        },
    )



# The three resolutions are separate endpoints because they are separate
# decisions. A single /resolve taking an action name made "dismiss" and
# "suppress" look like variations of one act, and let a caller send a title to
# an endpoint that has nowhere to put one.


@router.post("/api/admin/lead-reports/{report_id}/dismiss")
def dismiss_lead_report(
    report_id: uuid.UUID,
    payload: LeadReportNote,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Judge the report unfounded. Releases the hold; the Lead is untouched."""
    report = _open_report(db, report_id)
    try:
        dismiss_report(
            db,
            report,
            actor_id=principal.user_id,
            note=payload.note,
        )
    except ControlConflict as conflict:
        db.rollback()
        raise HTTPException(status_code=409, detail=conflict.message) from None
    _report_activity(
        db,
        report,
        action="dismissed",
        principal=principal,
        note=payload.note,
        after={"eligibilityHeld": False, "leadChanged": False},
    )
    db.commit()
    return {
        "reportId": str(report.id),
        "status": report.status,
        "eligibilityHeld": False,
    }


@router.post("/api/admin/lead-reports/{report_id}/correct")
def correct_lead_report(
    report_id: uuid.UUID,
    payload: LeadReportCorrect,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Override the Lead's delivered values in answer to the report."""
    report = _open_report(db, report_id)
    try:
        correction = correct_from_report(
            db,
            report,
            actor_id=principal.user_id,
            note=payload.note,
            title=payload.title,
            state=payload.state,
        )
    except ControlConflict as conflict:
        db.rollback()
        raise HTTPException(status_code=422, detail=conflict.message) from None
    _report_activity(
        db,
        report,
        action="corrected",
        principal=principal,
        note=payload.note,
        after={
            "eligibilityHeld": False,
            "correctionId": str(correction.id),
        },
    )
    # Correcting a Lead is consequential in its own right, so it is recorded
    # on the Lead as well. Reading only the report's Activity would otherwise
    # leave the Lead's own history with an unexplained change of values.
    _correction_activity(
        db,
        correction,
        principal=principal,
        reason=payload.note,
        origin={"leadReportId": str(report.id)},
    )
    db.commit()
    return {
        "reportId": str(report.id),
        "status": report.status,
        "correctionId": str(correction.id),
        "title": correction.title,
        "state": correction.state,
        "evidence": _evidence_payload(correction),
    }


@router.post("/api/admin/lead-reports/{report_id}/suppress")
def suppress_lead_report(
    report_id: uuid.UUID,
    payload: LeadReportNote,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Convert the report into audited Lead Suppression."""
    report = _open_report(db, report_id)
    try:
        lead = suppress_from_report(
            db,
            report,
            actor_id=principal.user_id,
            note=payload.note,
        )
    except ControlConflict as conflict:
        db.rollback()
        raise HTTPException(status_code=409, detail=conflict.message) from None
    _report_activity(
        db,
        report,
        action="suppressed",
        principal=principal,
        note=payload.note,
        after={"eligibilityHeld": False, "leadSuppressed": True},
    )
    # Suppression is an eligibility change, so it is recorded against the Lead
    # under the same action name a direct suppression uses.
    record_activity(
        db,
        action="lead_suppressed",
        target_type="lead",
        target_id=lead.id,
        actor_id=principal.user_id,
        reason=payload.note,
        details={
            "before": {"suppressed": False},
            "after": {"suppressed": True},
            "leadReportId": str(report.id),
        },
    )
    db.commit()
    return {
        "reportId": str(report.id),
        "status": report.status,
        "leadId": lead.id,
        "suppressed": True,
        "restoreNotice": RESTORE_NOTICE,
    }
