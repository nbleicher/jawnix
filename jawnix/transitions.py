from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .activity import record_activity
from .fulfillment import RequestActionContext, available_action_names
from .jobs import enqueue_job
from .milestone_emails import enqueue_milestone_email
from .models import (
    BatchArtifact,
    DistributionEvent,
    LeadRequest,
    RequestStatus,
    utcnow,
)


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
    # Enforcement asks the same question the administrator read contract asks,
    # against the same table, so the set a screen may offer and the set this
    # function accepts are the same set rather than two that agree today. See
    # jawnix/fulfillment.py.
    artifact = db.scalar(
        select(BatchArtifact).where(BatchArtifact.request_id == item.id)
    )
    committed = db.scalar(
        select(func.count(DistributionEvent.id)).where(
            DistributionEvent.request_id == item.id
        )
    )
    context = RequestActionContext(
        status=item.status,
        has_artifact=artifact is not None,
        committed_distributions=committed or 0,
    )
    if action == "retry_delivery" and artifact is None:
        # Worth its own sentence: the state is right, the evidence is missing.
        raise TransitionError("No generated artifact is available for delivery retry.")
    if action not in available_action_names(context):
        raise TransitionError(f"Action {action} is not valid while request is {item.status}.")
    if action == "approve":
        item.status = RequestStatus.approved.value
        item.approved_at = utcnow()
        item.status_message = "Approved; allocation is queued."
        enqueue_milestone_email(db, item)
        enqueue_job(db, "update_notification", item.id)
        enqueue_job(db, "fulfill_round_robin")
    elif action == "retry":
        item.status = RequestStatus.approved.value
        item.status_message = "Retry approved; allocation is queued."
        # The request is moving again, so it no longer has a stopping point.
        item.closed_at = None
        enqueue_milestone_email(db, item)
        enqueue_job(db, "update_notification", item.id)
        enqueue_job(db, "fulfill_round_robin")
    elif action == "retry_delivery":
        item.status = RequestStatus.generated.value
        item.status_message = "Delivery retry queued."
        item.closed_at = None
        enqueue_job(db, "update_notification", item.id)
        enqueue_job(db, "deliver_request", item.id)
    elif action == "reject":
        item.status = RequestStatus.rejected.value
        item.status_message = "Rejected by admin."
        item.closed_at = utcnow()
        enqueue_milestone_email(db, item)
        enqueue_job(db, "update_notification", item.id)
    elif action == "cancel":
        # A Canceled Request is withdrawn before any Distribution Event
        # commits. The table's precondition checks that under the request's row
        # lock, because an approved request can be mid-rotation and status
        # alone would not settle it. Cancellation is terminal and cannot
        # release Leads from an already generated batch.
        item.status = RequestStatus.canceled.value
        item.status_message = "Canceled by admin."
        enqueue_job(db, "update_notification", item.id)
    else:  # pragma: no cover - the table and this branch list are one commit apart
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
