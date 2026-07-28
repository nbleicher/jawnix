from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from .activity import record_activity
from .jobs import enqueue_job
from .models import BatchArtifact, LeadRequest, RequestStatus, utcnow


class TransitionError(Exception):
    def __init__(self, detail: str, status_code: int = 409):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def transition_request(
    db: Session,
    request_id: uuid.UUID,
    action: str,
    *,
    actor_id: str = "system:request-transition",
    reason: str = "Automated Batch Request transition",
) -> LeadRequest:
    item = db.scalar(select(LeadRequest).where(LeadRequest.id == request_id).with_for_update())
    if item is None:
        raise TransitionError("Request was not found.", 404)
    previous_status = item.status
    if action == "approve" and item.status == RequestStatus.pending.value:
        item.status = RequestStatus.approved.value
        item.approved_at = utcnow()
        item.status_message = "Approved; allocation is queued."
        enqueue_job(db, "update_notification", item.id)
        enqueue_job(db, "fulfill_round_robin")
    elif action == "retry" and item.status in {RequestStatus.waiting_inventory.value, RequestStatus.failed.value}:
        item.status = RequestStatus.approved.value
        item.status_message = "Retry approved; allocation is queued."
        # The request is moving again, so it no longer has a stopping point.
        item.closed_at = None
        enqueue_job(db, "update_notification", item.id)
        enqueue_job(db, "fulfill_round_robin")
    elif action == "retry_delivery" and item.status == RequestStatus.failed.value:
        if db.scalar(select(BatchArtifact).where(BatchArtifact.request_id == item.id)) is None:
            raise TransitionError("No generated artifact is available for delivery retry.")
        item.status = RequestStatus.generated.value
        item.status_message = "Delivery retry queued."
        item.closed_at = None
        enqueue_job(db, "update_notification", item.id)
        enqueue_job(db, "deliver_request", item.id)
    elif action == "reject" and item.status in {RequestStatus.pending.value, RequestStatus.waiting_inventory.value}:
        item.status = RequestStatus.rejected.value
        item.status_message = "Rejected by admin."
        item.closed_at = utcnow()
        enqueue_job(db, "update_notification", item.id)
    else:
        raise TransitionError(f"Action {action} is not valid while request is {item.status}.")
    record_activity(
        db,
        action=f"batch_request_{action}",
        target_type="batch_request",
        target_id=item.id,
        actor_id=actor_id,
        reason=reason,
        details={
            "before": {"status": previous_status},
            "after": {"status": item.status},
        },
    )
    db.flush()
    return item
