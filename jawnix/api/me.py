from __future__ import annotations

import uuid
import zipfile
from urllib.parse import quote
from fastapi import (
    Depends,
    HTTPException,
    Query,
    Response,
)
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..activity import record_activity
from ..auth import Principal, require_principal
from ..config import Settings, get_settings
from ..customer_history import request_history_scope
from ..customer_overview import CustomerHome, build_customer_home, parse_month_start
from ..customer_requests import (
    RequestSubmissionError,
    build_request_workspace,
    cancel_request as withdraw_request,
    customer_batch,
    find_customer_batch,
    submit_request,
)
from ..batch_copies import NewCopyRefused, request_new_copy
from ..database import get_db
from ..licensed_states import (
    LicensedStateApplyResult,
    LicensedStateConflict,
    LicensedStateConfirmation,
    LicensedStateReview,
    LicensedStateReviewError,
    LicensedStateSelection,
    LicensedStateWorkspace,
    apply_review as apply_licensed_state_review,
    preview as preview_licensed_states,
    workspace as licensed_state_workspace,
)
from ..models import (
    CustomerProfile,
    DistributionEvent,
    LeadDispositionState,
    LeadReport,
    LeadRequest,
    RequestStatus,
    utcnow,
)
from ..schemas import (
    LeadReportCreate,
    FeedbackLookup,
    ProfileOut,
    ProfileUpdate,
    CustomerRequestCreate,
    CustomerRequestDetail,
    CustomerRequestReceipt,
    CustomerRequestWorkspaceOut,
)
from ..states import normalize_phone, normalize_states
from ..fulfillment import artifact_available, artifact_download_filename
from ._shared import _current_customer_profile
from fastapi import APIRouter


router = APIRouter()


@router.get("/api/me/profile", response_model=ProfileOut)
def get_profile(principal: Principal = Depends(require_principal), db: Session = Depends(get_db)):
    return _current_customer_profile(db, principal)


@router.get("/api/me/licensed-states", response_model=LicensedStateWorkspace)
def get_licensed_state_workspace(
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
):
    return licensed_state_workspace(_current_customer_profile(db, principal))


@router.post(
    "/api/me/licensed-states/preview",
    response_model=LicensedStateReview,
)
def preview_licensed_state_changes(
    payload: LicensedStateSelection,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    try:
        return preview_licensed_states(
            db,
            user_id=principal.user_id,
            profile=_current_customer_profile(db, principal),
            selection=payload,
            settings=settings,
        )
    except LicensedStateConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except LicensedStateReviewError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.post(
    "/api/me/licensed-states/apply",
    response_model=LicensedStateApplyResult,
)
def apply_licensed_state_changes(
    payload: LicensedStateConfirmation,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    # Refuse replaced accounts before validating even a signed review.
    _current_customer_profile(db, principal)
    try:
        return apply_licensed_state_review(
            db,
            user_id=principal.user_id,
            confirmation=payload,
            settings=settings,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except LicensedStateConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except LicensedStateReviewError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.get("/api/me/overview", response_model=CustomerHome)
def get_customer_home(
    month_start: str | None = Query(default=None, max_length=64),
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
):
    """The Customer's Home in one read, and the Customer shell's access check.

    ``month_start`` is the start of the browser's local month, so "this month"
    follows the Customer's calendar; without it the UTC month applies.
    """

    return build_customer_home(
        db,
        user_id=principal.user_id,
        profile=_current_customer_profile(db, principal),
        month_start=parse_month_start(month_start),
    )


@router.get("/api/me/batch-requests", response_model=CustomerRequestWorkspaceOut)
def get_batch_request_workspace(
    page: int = Query(default=1, ge=1, le=100_000),
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
):
    """Everything the guided Batch Request screen needs, in one read."""

    return build_request_workspace(
        db,
        user_id=principal.user_id,
        profile=_current_customer_profile(db, principal),
        page=page,
    )


@router.get("/api/me/batch-requests/{reference}", response_model=CustomerRequestDetail)
def customer_batch_request_detail(
    reference: str,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
):
    """One batch by number ("1042") or by the UUID older links carry."""

    profile = _current_customer_profile(db, principal)
    item = find_customer_batch(
        db, reference, request_history_scope(profile, principal.user_id)
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Request was not found.")
    return customer_batch(db, item, with_states=True)


@router.post(
    "/api/me/batch-requests/{request_id}/new-copy",
    response_model=CustomerRequestDetail,
    status_code=202,
)
def request_batch_new_copy(
    request_id: uuid.UUID,
    response: Response,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
):
    """Ask for a new copy of an expired batch file.

    The same leads, rebuilt byte for byte by the worker and emailed when
    ready. Asking again while a copy is on the way answers 200 with it.
    """

    if principal.role != "customer" or principal.audience != "customer":
        raise HTTPException(status_code=403, detail="Customer access required.")
    profile = _current_customer_profile(db, principal)
    item = find_customer_batch(
        db,
        str(request_id),
        request_history_scope(profile, principal.user_id),
        for_update=True,
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Request was not found.")
    try:
        _, created = request_new_copy(db, item=item, actor_id=principal.user_id)
    except NewCopyRefused as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    db.commit()
    if not created:
        response.status_code = 200
    return customer_batch(db, item, with_states=True)


@router.post(
    "/api/me/batch-requests",
    response_model=CustomerRequestReceipt,
    status_code=201,
    responses={
        200: {
            "model": CustomerRequestReceipt,
            "description": "This submission key already created a Batch Request.",
        }
    },
)
def submit_batch_request(
    payload: CustomerRequestCreate,
    response: Response,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
):
    """Submit the reviewed request, at most once per submission key.

    A replay answers 200 with the request the first attempt created, so a
    double-click or a retried POST is indistinguishable from a single one.
    """

    profile = _current_customer_profile(db, principal)
    try:
        item, created = submit_request(
            db,
            user_id=principal.user_id,
            profile=profile,
            payload=payload,
        )
    except RequestSubmissionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    if not created:
        response.status_code = 200
    return CustomerRequestReceipt(created=created, request=customer_batch(db, item))


@router.post(
    "/api/me/batch-requests/{request_id}/cancel",
    response_model=CustomerRequestDetail,
)
def cancel_batch_request(
    request_id: uuid.UUID,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
):
    """Withdraw a Batch Request and return its updated milestone graph."""

    # Called for its refusals: a replaced User Account may not withdraw work
    # belonging to the identity it replaced.
    _current_customer_profile(db, principal)
    try:
        item = withdraw_request(
            db,
            user_id=principal.user_id,
            request_id=request_id,
        )
    except RequestSubmissionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    return customer_batch(db, item)


@router.get("/api/me/batch-requests/{request_id}/artifact")
def download_batch_artifact(
    request_id: uuid.UUID,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
):
    """Download this Customer's live artifact and record the retrieval."""

    if principal.role != "customer" or principal.audience != "customer":
        raise HTTPException(status_code=403, detail="Customer access required.")
    profile = _current_customer_profile(db, principal)
    item = db.scalar(
        select(LeadRequest).where(
            LeadRequest.id == request_id,
            LeadRequest.agent_id == profile.customer_id,
            LeadRequest.status == RequestStatus.delivered.value,
        )
    )
    # A missing request, another Customer's request, and a request that has not
    # been delivered are intentionally indistinguishable at this boundary.
    if item is None or item.artifact is None:
        raise HTTPException(status_code=404, detail="Batch Artifact was not found.")
    artifact = item.artifact
    if not artifact_available(artifact):
        raise HTTPException(
            status_code=410,
            detail="This file has expired. Get a new copy from the batch page.",
        )

    download_filename = artifact_download_filename(artifact)
    artifact_path = artifact.path
    record_activity(
        db,
        action="batch_artifact_downloaded",
        target_type="batch_request",
        target_id=item.id,
        actor_id=principal.user_id,
        reason="Customer downloaded the live Batch Artifact.",
        details={
            "artifactId": artifact.id,
            "requestId": str(item.id),
            "filename": download_filename,
            "storedFilename": artifact.filename,
            "rowCount": artifact.row_count,
            "parts": artifact.parts,
            "expiresAt": (
                artifact.expires_at.isoformat()
                if artifact.expires_at is not None
                else None
            ),
        },
    )
    db.commit()
    headers = {
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
    }
    if download_filename != artifact.filename:
        def csv_chunks():
            with zipfile.ZipFile(artifact_path) as archive:
                with archive.open(download_filename) as stream:
                    while chunk := stream.read(64 * 1024):
                        yield chunk

        return StreamingResponse(
            csv_chunks(),
            media_type="text/csv",
            headers={
                **headers,
                "Content-Disposition": f"attachment; filename*=utf-8''{quote(download_filename)}",
            },
        )
    return FileResponse(
        artifact_path,
        media_type=(
            "application/zip"
            if artifact.filename.lower().endswith(".zip")
            else "text/csv"
        ),
        filename=download_filename,
        content_disposition_type="attachment",
        headers=headers,
    )


@router.patch("/api/me/profile", response_model=ProfileOut)
def update_profile(
    payload: ProfileUpdate,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
):
    _current_customer_profile(db, principal)
    profile = db.scalar(
        select(CustomerProfile)
        .where(CustomerProfile.user_id == principal.user_id)
        .with_for_update()
    )
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile was not found.")
    previous_states = normalize_states(profile.licensed_states)
    next_states = normalize_states(payload.licensed_states)
    if next_states != previous_states:
        raise HTTPException(
            status_code=409,
            detail=(
                "Appointed state changes need a review. "
                "Change them on Account."
            ),
        )

    profile.first_name = payload.first_name.strip()
    profile.last_name = payload.last_name.strip()
    profile.phone = payload.phone.strip()
    profile.updated_at = utcnow()
    db.commit()
    db.refresh(profile)
    return profile


def _feedback_not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail="No delivered Lead found.",
    )


@router.post("/api/me/feedback/lookup")
def lookup_feedback(
    payload: FeedbackLookup,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
):
    profile = _current_customer_profile(db, principal)
    phone = normalize_phone(payload.phone)
    if (
        profile is None
        or profile.customer_id is None
        or phone is None
    ):
        raise _feedback_not_found()
    event = db.scalar(
        select(DistributionEvent)
        .where(
            DistributionEvent.agent_id == profile.customer_id,
            DistributionEvent.phone == phone,
            *(
                [DistributionEvent.request_id == payload.request_id]
                if payload.request_id is not None
                else []
            ),
        )
        .order_by(
            DistributionEvent.delivered_at.desc(),
            DistributionEvent.id.desc(),
        )
        .limit(1)
    )
    if event is None:
        raise _feedback_not_found()
    disposition_state = db.get(
        LeadDispositionState,
        event.id,
    )
    return {
        "distributionEventId": event.id,
        "businessName": event.title,
        "phone": event.phone,
        "deliveredAt": event.delivered_at,
        "batchId": (
            str(event.request_id)
            if event.request_id is not None
            else None
        ),
        "currentDisposition": (
            disposition_state.current_disposition
            if disposition_state is not None
            else None
        ),
    }


def _customer_distribution(
    db: Session,
    principal: Principal,
    event_id: int,
) -> tuple[CustomerProfile, DistributionEvent]:
    profile = _current_customer_profile(db, principal)
    if profile is None or profile.customer_id is None:
        raise HTTPException(status_code=404, detail="Customer was not found.")
    event = db.scalar(
        select(DistributionEvent).where(
            DistributionEvent.id == event_id,
            DistributionEvent.agent_id == profile.customer_id,
        )
    )
    if event is None:
        raise HTTPException(status_code=404, detail="Delivered Lead was not found.")
    return profile, event


@router.post(
    "/api/me/distributions/{event_id}/reports",
    status_code=201,
)
def create_lead_report(
    event_id: int,
    payload: LeadReportCreate,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
):
    profile, event = _customer_distribution(db, principal, event_id)
    report = LeadReport(
        distribution_event_id=event.id,
        customer_id=profile.customer_id,
        reason=payload.reason,
        details=payload.details.strip(),
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return {
        "id": str(report.id),
        "distributionEventId": report.distribution_event_id,
        "reason": report.reason,
        "details": report.details,
        "status": report.status,
        "createdAt": report.created_at,
    }
