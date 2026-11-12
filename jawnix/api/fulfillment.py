from __future__ import annotations

import uuid
from fastapi import Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..activity import record_activity
from ..auth import Principal, require_admin, require_principal
from ..config import Settings, get_settings
from ..batch_copies import ArtifactRebuildError, rebuild_expired_artifact
from ..database import get_db
from ..jobs import enqueue_job
from ..models import LeadRequest
from ..schemas import ActionReason
from ..fulfillment import (
    TRANSITION_ACTIONS,
    describe_request,
    workspace as fulfillment_workspace,
)
from ..transitions import TransitionError, transition_request
from ..request_updates import request_events
from fastapi import APIRouter


router = APIRouter()


def _request_dict(item: LeadRequest) -> dict:
    return {
        "id": str(item.id),
        "number": item.number,
        "userId": str(item.user_id),
        "customer": " ".join(part for part in (item.profile.first_name, item.profile.last_name) if part).strip() or item.profile.email,
        "email": item.delivery_email,
        "customerIdentity": item.customer.name,
        "leadCount": item.lead_count,
        "rowsPerFile": item.rows_per_file,
        "states": item.states_snapshot,
        "status": item.status,
        "availableCount": item.available_count,
        "statusMessage": item.status_message,
        "createdAt": item.created_at,
        "deliveredAt": item.delivered_at,
        "hasArtifact": item.artifact is not None,
    }


@router.get("/api/admin/fulfillment")
def admin_fulfillment(
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """The one aggregate the Fulfillment workspace reads.

    Gathering outstanding Batch Requests, pending Inventory Conflicts, and
    delivery failures here keeps the screen from stitching together several
    unrelated browser requests, and gives every item the actions its current
    state actually permits.
    """
    return fulfillment_workspace(db)


@router.get("/api/admin/requests/{request_id}")
def admin_request_detail(
    request_id: uuid.UUID,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    item = db.get(LeadRequest, request_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Request was not found.")
    return describe_request(db, item)


@router.get("/api/admin/requests/{request_id}/events")
def admin_batch_request_events(
    request_id: uuid.UUID,
    request: Request,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    if db.get(LeadRequest, request_id) is None:
        raise HTTPException(status_code=404, detail="Request was not found.")
    db.close()
    async def authorize():
        try:
            await require_admin(require_principal(request, settings), settings, db)
        finally:
            db.close()
    return StreamingResponse(request_events(request, {str(request_id)}, authorize), media_type="text/event-stream", headers={"Cache-Control":"private, no-store", "X-Accel-Buffering":"no"})


@router.post("/api/admin/requests/{request_id}/notify")
def renotify_batch_request(
    request_id: uuid.UUID,
    payload: ActionReason,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Queue a Telegram post/edit for a Batch Request that needs one.

    Covers both "never notified" (no Notification row) and "message went
    stale" (row exists but the chat message was deleted). ``update_notification``
    is idempotent either way. Deliberately not a status transition — the
    request itself is unchanged; only the Telegram surface is repaired.

    Registered before ``/{action}`` so ``notify`` is not swallowed as an
    unknown transition action.
    """
    item = db.scalar(
        select(LeadRequest)
        .where(LeadRequest.id == request_id)
        .with_for_update()
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Request was not found.")
    job = enqueue_job(db, "update_notification", item.id)
    record_activity(
        db,
        action="batch_request_notify",
        target_type="batch_request",
        target_id=item.id,
        actor_id=principal.user_id,
        reason=payload.reason,
        details={
            "requestId": str(item.id),
            "jobId": job.id,
        },
    )
    db.commit()
    return {
        "requestId": str(item.id),
        "jobId": job.id,
        "jobKind": job.kind,
    }


@router.post("/api/admin/requests/{request_id}/{action}")
def admin_request_action(
    request_id: uuid.UUID,
    action: str,
    payload: ActionReason,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    # The reason is required, not defaulted. A synthesised "Administrator
    # requested approve." records that something happened while explaining
    # nothing, which is the failure an audit trail exists to prevent — and it
    # made the contract's `requiresReason` a claim the API did not keep.
    if action not in TRANSITION_ACTIONS:
        raise HTTPException(status_code=404, detail="Unknown action.")
    try:
        item = transition_request(
            db,
            request_id,
            action,
            actor_id=str(principal.user_id),
            reason=payload.reason,
        )
    except TransitionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    db.commit()
    return {**_request_dict(item), **describe_request(db, item)}


@router.post("/api/admin/requests/{request_id}/artifact/regenerate")
def regenerate_batch_artifact(
    request_id: uuid.UUID,
    payload: ActionReason,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    item = db.scalar(
        select(LeadRequest)
        .where(LeadRequest.id == request_id)
        .with_for_update()
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Request was not found.")
    try:
        artifact, previous_artifact = rebuild_expired_artifact(db, item, settings)
    except ArtifactRebuildError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    record_activity(
        db,
        action="batch_artifact_regenerated",
        target_type="batch_request",
        target_id=item.id,
        actor_id=principal.user_id,
        reason=payload.reason,
        details={
            "before": previous_artifact,
            "after": {
                "artifactId": artifact.id,
                "available": True,
            },
            "requestId": str(item.id),
            "sha256": artifact.sha256,
            "expiresAt": (
                artifact.expires_at.isoformat()
                if artifact.expires_at is not None
                else None
            ),
        },
    )
    db.commit()
    return {
        "artifactId": artifact.id,
        "requestId": str(item.id),
        "sha256": artifact.sha256,
        "expiresAt": artifact.expires_at,
    }
