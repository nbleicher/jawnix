"""The single authority on which Fulfillment actions a Batch Request offers.

Issue #57 requires that Approve, Retry, Reject, Cancel, Regenerate, and Retry
delivery appear only in valid states, on the grounds that an invalid action
offered is a bug rather than a UI detail. That is only achievable if the
surface that *offers* actions and the surface that *enforces* them read the
same table. Before this module there were three independent copies of the rule
-- ``transitions.transition_request``, ``admin.html``'s button rendering, and
``telegram._keyboard`` -- and nothing kept them agreeing.

``transition_request`` and the administrator read contract now both go through
:func:`available_actions`, so the set a screen may render and the set the
domain will accept are the same set by construction rather than by agreement.

``admin.html`` is deliberately *not* migrated: it is the legacy surface the
controlled cutover (#71) retires, and it keeps its own copy until then. It
reads the same endpoints, so it can only ever offer a subset of what this
table permits — it cannot get a refusal wrong, only miss an option.

The vocabulary follows CONTEXT.md: Request Approval authorizes a Batch
Request's *first* allocation attempt, a Canceled Request is one withdrawn
before any Distribution Event commits, and a Batch Artifact is the exact CSV
materialization whose expired file an administrator may regenerate.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, nullslast, or_, select
from sqlalchemy.orm import Session

from .eligibility import active_holds, report_summaries, suppressed_leads
from .milestones import build_milestones
from .models import (
    AuditEntry,
    BatchArtifact,
    DistributionEvent,
    InventoryConflict,
    Job,
    JobStatus,
    LeadRequest,
    Notification,
    RequestStatus,
)


@dataclass(frozen=True)
class RequestActionContext:
    """Everything the table needs to decide what a Batch Request may offer.

    Deliberately plain data rather than a ``LeadRequest``: the same decision has
    to be reachable from a read projection, from enforcement inside a
    transaction, and from tests, without three different ways to ask.
    """

    status: str
    #: A Batch Artifact row exists, whether or not its file is still on disk.
    has_artifact: bool = False
    #: The artifact's file is present and has not passed its 30-day expiry.
    artifact_available: bool = False
    #: Distribution Events already committed for this request. Permanent once
    #: written, which is what puts Cancellation out of reach.
    committed_distributions: int = 0
    #: Committed Distribution Events cover the full requested quantity.
    distribution_complete: bool = False
    #: A Telegram message with a live inline keyboard exists for this request.
    #: Rendered as provenance; it never changes which actions are offered.
    telegram_decision_pending: bool = False


def _always(_: "RequestActionContext") -> bool:
    return True


@dataclass(frozen=True)
class FulfillmentAction:
    """One administrator action, with the copy needed to confirm it."""

    name: str
    label: str
    #: Plain-language statement of what happens, shown as the confirmation
    #: dialog's description. Required by ConfirmDialog, and the thing that
    #: keeps Retry notification and Retry generation from reading alike.
    consequence: str
    #: Statuses the underlying transition accepts. Empty for actions that are
    #: not status transitions (regeneration rebuilds a file and leaves the
    #: Batch Request's status alone).
    from_statuses: frozenset[str] = field(default_factory=frozenset)
    #: Every action here changes durable state a person could later need
    #: explained, so all of them record a reason through record_activity().
    requires_reason: bool = True
    #: Irreversible in the domain's terms: Rejection and Cancellation are
    #: terminal and cannot release Leads from an already generated batch.
    destructive: bool = False
    #: Everything beyond the status check. Kept beside the action rather than
    #: in a cascade so a new action carries its own rule in one place.
    precondition: Callable[["RequestActionContext"], bool] = _always


APPROVE = FulfillmentAction(
    name="approve",
    label="Approve",
    consequence=(
        "Authorizes this Batch Request's first allocation attempt and queues "
        "it for Fulfillment Rotation."
    ),
    from_statuses=frozenset({RequestStatus.pending.value}),
)

RETRY = FulfillmentAction(
    name="retry",
    label="Retry generation",
    consequence=(
        "Returns this Batch Request to the approved queue and runs allocation "
        "again. It may select different Leads than a previous attempt."
    ),
    from_statuses=frozenset(
        {RequestStatus.waiting_inventory.value, RequestStatus.failed.value}
    ),
    # A failed Batch Request that already produced an artifact has permanent
    # Distribution Events (docs/adr/0003), and delivery failure never returns
    # its Leads to inventory. Reallocating would consume *more* inventory for
    # the same request, so only the exact-artifact path is safe once one exists.
    precondition=lambda ctx: not ctx.has_artifact,
)

RETRY_DELIVERY = FulfillmentAction(
    name="retry_delivery",
    label="Retry notification",
    consequence=(
        "Emails another portal notification for the exact existing Batch "
        "Artifact. Nothing is reallocated and the delivered Leads do not "
        "change."
    ),
    from_statuses=frozenset({RequestStatus.failed.value}),
    # There is nothing to resend without one.
    precondition=lambda ctx: ctx.has_artifact,
)

REJECT = FulfillmentAction(
    name="reject",
    label="Reject",
    consequence=(
        "Refuses this Batch Request. Rejection is terminal and the Customer "
        "must submit a new request."
    ),
    from_statuses=frozenset(
        {RequestStatus.pending.value, RequestStatus.waiting_inventory.value}
    ),
    destructive=True,
)

CANCEL = FulfillmentAction(
    name="cancel",
    label="Cancel",
    consequence=(
        "Withdraws this Batch Request before any Distribution Event commits. "
        "Cancellation is terminal."
    ),
    from_statuses=frozenset(
        {
            RequestStatus.pending.value,
            RequestStatus.approved.value,
            RequestStatus.waiting_inventory.value,
        }
    ),
    destructive=True,
    # "Withdrawn before any Distribution Event commits" is the definition, and
    # an approved request can be mid-rotation: status alone does not settle it.
    precondition=lambda ctx: ctx.committed_distributions == 0,
)

REGENERATE = FulfillmentAction(
    name="regenerate",
    label="Regenerate artifact",
    consequence=(
        "Rebuilds the exact expired CSV from its committed Distribution "
        "Events, starting a new 30-day retention period. The file's contents "
        "and checksum are unchanged."
    ),
    # Mirrors the endpoint: the file must have expired or gone missing, and the
    # committed Distribution Events it is rebuilt from must be whole.
    precondition=lambda ctx: (
        ctx.has_artifact
        and not ctx.artifact_available
        and ctx.distribution_complete
    ),
)

#: Declaration order is offer order, so every surface lists actions the same way.
ACTIONS: tuple[FulfillmentAction, ...] = (
    APPROVE,
    RETRY,
    RETRY_DELIVERY,
    REGENERATE,
    REJECT,
    CANCEL,
)

_BY_NAME = {action.name: action for action in ACTIONS}

#: The subset ``transition_request`` owns. Regeneration has its own endpoint
#: because it rebuilds a file rather than moving the Batch Request's status.
TRANSITION_ACTIONS: frozenset[str] = frozenset(
    action.name for action in ACTIONS if action.from_statuses
)


def action_named(name: str) -> FulfillmentAction:
    """Return the action, or raise ``KeyError`` for an unknown name."""
    return _BY_NAME[name]


def transition_rule(name: str) -> FulfillmentAction | None:
    """Return the transition rule for ``name``, or ``None`` if it is not one."""
    action = _BY_NAME.get(name)
    if action is None or not action.from_statuses:
        return None
    return action


def _is_offered(action: FulfillmentAction, ctx: RequestActionContext) -> bool:
    """A status the action accepts, plus whatever else that action requires.

    An action with no ``from_statuses`` is not a status transition, so only its
    precondition governs.
    """
    if action.from_statuses and ctx.status not in action.from_statuses:
        return False
    return action.precondition(ctx)


def available_actions(
    ctx: RequestActionContext,
) -> tuple[FulfillmentAction, ...]:
    """Every action valid for this Batch Request right now, in offer order."""
    return tuple(action for action in ACTIONS if _is_offered(action, ctx))


def available_action_names(ctx: RequestActionContext) -> tuple[str, ...]:
    return tuple(action.name for action in available_actions(ctx))


def describe_actions(ctx: RequestActionContext) -> list[dict[str, object]]:
    """Project the available actions for the administrator read contract."""
    return [_describe(action) for action in available_actions(ctx)]


def _describe(action: FulfillmentAction) -> dict[str, object]:
    return {
        "name": action.name,
        "label": action.label,
        "consequence": action.consequence,
        "requiresReason": action.requires_reason,
        "destructive": action.destructive,
    }


#: The subset Telegram's inline keyboard may carry. Cancellation and
#: regeneration are deliberately absent: both need a typed reason that a
#: callback button cannot collect.
TELEGRAM_ACTIONS: frozenset[str] = frozenset(
    {"approve", "retry", "retry_delivery", "reject"}
)


def telegram_actions(ctx: RequestActionContext) -> tuple[FulfillmentAction, ...]:
    """The actions Telegram offers, drawn from the same table as the screen.

    Reading both keyboards from one table is what keeps #57's "rendered without
    duplicate action opportunities" true: the two channels cannot come to
    disagree about what is on offer, so there is one decision to make rather
    than two competing ones.
    """
    return tuple(
        action for action in available_actions(ctx) if action.name in TELEGRAM_ACTIONS
    )


# --------------------------------------------------------------------------
# Reading the workspace
#
# These build the administrator read contract. They project the table above
# rather than re-deriving validity, so the screen and the enforcement path
# cannot disagree.
# --------------------------------------------------------------------------


def _as_utc(value: datetime | None) -> datetime | None:
    """Read a stored timestamp as UTC.

    SQLite hands back naive datetimes for the timezone-aware values written
    through ``utcnow()``, so comparing a reloaded ``expires_at`` against an
    aware ``now`` raises. Treat a naive reading as the UTC it was written as.
    """
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def artifact_available(
    artifact: BatchArtifact | None, *, now: datetime | None = None
) -> bool:
    """Whether a Batch Artifact's file can still be delivered as it stands.

    The single definition of "has not expired". The regeneration endpoint
    refuses while this is true and the offer surface withholds Regenerate while
    it is true, so they have to be reading the same predicate or the workspace
    will offer an action the endpoint rejects.
    """
    if artifact is None or not Path(artifact.path).is_file():
        return False
    expires_at = _as_utc(artifact.expires_at)
    return expires_at is not None and expires_at > (
        now or datetime.now(timezone.utc)
    )


def request_context(
    db: Session,
    item: LeadRequest,
    *,
    now: datetime | None = None,
) -> RequestActionContext:
    """Gather what the action table needs to judge one Batch Request."""
    moment = now or datetime.now(timezone.utc)
    artifact = item.artifact
    committed = db.scalar(
        select(func.count(DistributionEvent.id)).where(
            DistributionEvent.request_id == item.id
        )
    )
    notified = db.scalar(
        select(func.count(Notification.id)).where(Notification.request_id == item.id)
    )
    context = RequestActionContext(
        status=item.status,
        has_artifact=artifact is not None,
        artifact_available=artifact_available(artifact, now=moment),
        committed_distributions=committed or 0,
        distribution_complete=committed == item.lead_count,
    )
    # A Telegram message exists and its keyboard is still live for this state.
    return replace(
        context,
        telegram_decision_pending=bool(notified)
        and bool(telegram_actions(context)),
    )


def _summarize_request(
    item: LeadRequest, ctx: RequestActionContext
) -> dict[str, object]:
    return {
        "id": str(item.id),
        "customer": " ".join(
            part
            for part in (item.profile.first_name, item.profile.last_name)
            if part
        ).strip()
        or item.profile.email,
        "customerIdentity": item.customer.name,
        "agency": item.customer.agency.name if item.customer.agency else "",
        "email": item.delivery_email,
        "leadCount": item.lead_count,
        "rowsPerFile": item.rows_per_file,
        "states": item.states_snapshot,
        "stateMode": item.state_mode,
        "status": item.status,
        "statusMessage": item.status_message,
        "availableCount": item.available_count,
        "createdAt": item.created_at,
        "actions": describe_actions(ctx),
    }


def describe_request(
    db: Session, item: LeadRequest, *, now: datetime | None = None
) -> dict[str, object]:
    """The full Batch Request contract the detail screen reads."""
    ctx = request_context(db, item, now=now)
    artifact = item.artifact
    entries = db.scalars(
        select(AuditEntry)
        .where(
            AuditEntry.target_type == "batch_request",
            AuditEntry.target_id == str(item.id),
        )
        .order_by(AuditEntry.created_at.desc(), AuditEntry.id.desc())
    )
    live_jobs = list(
        db.scalars(
            select(Job)
            .where(
                Job.request_id == item.id,
                Job.status != JobStatus.complete.value,
            )
            .order_by(Job.created_at.asc(), Job.id.asc())
        )
    )
    # Recent Jobs (every status) plus Audit Entries become the Progress log —
    # newest first — so an administrator can scroll the trail of what ran.
    recent_jobs = list(
        db.scalars(
            select(Job)
            .where(Job.request_id == item.id)
            .order_by(Job.created_at.desc(), Job.id.desc())
            .limit(40)
        )
    )
    audit_entries = list(entries)
    by_state = _distribution_by_state(db, item.id)
    settled_status = item.status in _SETTLED_STATUSES
    live_active = any(
        job.status in {JobStatus.queued.value, JobStatus.running.value}
        for job in live_jobs
    )
    blocker = _live_worker_blocker(db, item, live_jobs)
    detail = _summarize_request(item, ctx)
    detail.update(
        {
            "approvedAt": item.approved_at,
            "deliveredAt": item.delivered_at,
            "artifact": (
                {
                    "filename": artifact.filename,
                    "rowCount": artifact.row_count,
                    "parts": [
                        {
                            "filename": part["filename"],
                            "rowCount": part["row_count"],
                        }
                        for part in artifact.parts
                    ],
                    "sha256": artifact.sha256,
                    "deliveryStatus": artifact.delivery_status,
                    "deliveryAttempts": artifact.delivery_attempts,
                    "lastError": artifact.last_error,
                    "expiresAt": artifact.expires_at,
                    "sentAt": artifact.sent_at,
                    "available": ctx.artifact_available,
                }
                if artifact is not None
                else None
            ),
            "distribution": {
                "committed": ctx.committed_distributions,
                "expected": item.lead_count,
                "complete": ctx.distribution_complete,
                "byState": by_state,
            },
            "milestones": build_milestones(item).model_dump(mode="json"),
            "live": {
                "settled": settled_status and not live_active,
                "refreshSeconds": 10,
                "blocker": blocker,
                "jobs": [
                    {
                        "kind": job.kind,
                        "label": _JOB_LABELS.get(
                            job.kind, _words(job.kind)
                        ),
                        "status": job.status,
                        "attempts": job.attempts,
                        "runAfter": job.run_after,
                        "lastError": job.last_error or None,
                    }
                    for job in live_jobs
                ],
                "log": _progress_log(
                    item,
                    recent_jobs=recent_jobs,
                    audits=audit_entries,
                ),
            },
            # Provenance, not a second action surface. The workspace renders
            # one action set and says a Telegram decision is also live.
            "telegram": {"decisionPending": ctx.telegram_decision_pending},
            "history": [
                {
                    "action": entry.action,
                    "actor": entry.actor_user_id,
                    "reason": entry.reason,
                    "recordedAt": entry.created_at,
                    "before": entry.details.get("before"),
                    "after": entry.details.get("after"),
                }
                for entry in audit_entries
            ],
        }
    )
    return detail


_SETTLED_STATUSES = frozenset(
    {
        RequestStatus.delivered.value,
        RequestStatus.rejected.value,
        RequestStatus.canceled.value,
        RequestStatus.failed.value,
    }
)

_JOB_LABELS: dict[str, str] = {
    "notify_request": "Sending the Telegram approval message",
    "update_notification": "Updating the Telegram message",
    "licensed_states_changed": "Refreshing the Telegram message",
    "allocate_request": "Allocating inventory",
    "deliver_request": "Delivering the Batch email",
    "fulfill_round_robin": "Running round-robin fulfillment",
    "emit_lead_assigned": "Emitting lead.assigned metrics",
    "send_request_milestone_email": "Sending a milestone email",
}

_LOG_JOB_TONES: dict[str, str] = {
    JobStatus.queued.value: "info",
    JobStatus.running.value: "info",
    JobStatus.complete.value: "success",
    JobStatus.failed.value: "danger",
}


def _live_worker_blocker(
    db: Session, item: LeadRequest, live_jobs: list[Job]
) -> dict[str, object] | None:
    """Explain why this request's jobs sit queued while the worker is elsewhere.

    Progress only lists jobs for *this* request. When the single worker is
    busy on another request (or a global job), those rows just say "queued"
    with no reason — which is what operators saw when emit_lead_assigned for
    a 50k batch blocked Telegram for hours.
    """
    queued_here = [
        job
        for job in live_jobs
        if job.status == JobStatus.queued.value
    ]
    if not queued_here:
        return None

    running = db.scalar(
        select(Job)
        .where(
            Job.status == JobStatus.running.value,
            or_(
                Job.request_id.is_(None),
                Job.request_id != item.id,
            ),
        )
        .order_by(nullslast(Job.locked_at), Job.id.asc())
        .limit(1)
    )
    blocker = running
    if blocker is None:
        earliest_ours = min(job.id for job in queued_here)
        blocker = db.scalar(
            select(Job)
            .where(
                Job.id < earliest_ours,
                Job.status.in_(
                    {
                        JobStatus.queued.value,
                        JobStatus.running.value,
                    }
                ),
                or_(
                    Job.request_id.is_(None),
                    Job.request_id != item.id,
                ),
            )
            .order_by(Job.run_after.asc(), Job.id.asc())
            .limit(1)
        )
    if blocker is None:
        return None

    label = _JOB_LABELS.get(blocker.kind, _words(blocker.kind))
    other = (
        f" for another batch ({blocker.request_id})"
        if blocker.request_id is not None
        else ""
    )
    return {
        "kind": blocker.kind,
        "label": label,
        "status": blocker.status,
        "jobId": blocker.id,
        "requestId": (
            str(blocker.request_id) if blocker.request_id is not None else None
        ),
        "detail": (
            f"The worker is busy with {label}{other}. "
            "Jobs for this request stay queued until that finishes."
        ),
    }


def _words(value: str) -> str:
    return value.replace("_", " ").capitalize()


def _progress_log(
    item: LeadRequest,
    *,
    recent_jobs: list[Job],
    audits: list[AuditEntry],
) -> list[dict[str, object]]:
    """Newest-first trail of Jobs and decisions for the Progress panel."""
    rows: list[dict[str, object]] = []
    rows.append(
        {
            "id": f"status:{item.id}",
            "at": item.processed_at
            or item.approved_at
            or item.created_at,
            "kind": "status",
            "label": item.status_message
            or status_label_for_log(item.status),
            "detail": status_label_for_log(item.status),
            "tone": _status_log_tone(item.status),
        }
    )
    for job in recent_jobs:
        detail_parts: list[str] = [job.status]
        if job.attempts > 1:
            detail_parts.append(f"attempt {job.attempts}")
        if job.last_error:
            detail_parts.append(job.last_error[:160])
        rows.append(
            {
                "id": f"job:{job.id}",
                "at": job.created_at,
                "kind": "job",
                "label": _JOB_LABELS.get(job.kind, _words(job.kind)),
                "detail": " · ".join(detail_parts),
                "tone": _LOG_JOB_TONES.get(job.status, "neutral"),
            }
        )
    for entry in audits:
        reason = (entry.reason or "").strip()
        rows.append(
            {
                "id": f"audit:{entry.id}",
                "at": entry.created_at,
                "kind": "decision",
                "label": _words(entry.action),
                "detail": reason or None,
                "tone": "neutral",
            }
        )
    rows.sort(
        key=lambda row: (
            _as_utc(row["at"] if isinstance(row["at"], datetime) else None)
            or datetime.min.replace(tzinfo=timezone.utc),
            str(row["id"]),
        ),
        reverse=True,
    )
    return rows[:50]


def status_label_for_log(status: str) -> str:
    return {
        RequestStatus.pending.value: "Pending",
        RequestStatus.approved.value: "Approved",
        RequestStatus.processing.value: "Processing",
        RequestStatus.waiting_inventory.value: "Waiting for inventory",
        RequestStatus.generated.value: "Generated",
        RequestStatus.delivered.value: "Delivered",
        RequestStatus.rejected.value: "Rejected",
        RequestStatus.canceled.value: "Canceled",
        RequestStatus.failed.value: "Failed",
    }.get(status, status)


def _status_log_tone(status: str) -> str:
    return {
        RequestStatus.pending.value: "info",
        RequestStatus.approved.value: "info",
        RequestStatus.processing.value: "info",
        RequestStatus.waiting_inventory.value: "warning",
        RequestStatus.generated.value: "info",
        RequestStatus.delivered.value: "success",
        RequestStatus.rejected.value: "danger",
        RequestStatus.canceled.value: "neutral",
        RequestStatus.failed.value: "danger",
    }.get(status, "neutral")


def _distribution_by_state(
    db: Session, request_id
) -> list[dict[str, object]]:
    """Composition of committed Distribution Events by state × Niche."""
    rows = db.execute(
        select(
            DistributionEvent.state,
            DistributionEvent.source_niche,
            func.count(DistributionEvent.id),
        )
        .where(DistributionEvent.request_id == request_id)
        .group_by(DistributionEvent.state, DistributionEvent.source_niche)
        .order_by(DistributionEvent.state, DistributionEvent.source_niche)
    ).all()
    return [
        {
            "state": state,
            "niche": niche or "Unmapped",
            "count": int(count),
        }
        for state, niche, count in rows
    ]


#: Inventory Conflict decisions keep their own vocabulary. They are not Batch
#: Request transitions: one decision authorizes one attempt against one
#: inventory snapshot (docs/adr/0004).
CONFIRM_CONFLICT = FulfillmentAction(
    name="confirm",
    label="Confirm once",
    consequence=(
        "Authorizes one allocation attempt for this exact inventory snapshot. "
        "The authorization is spent by that attempt whether or not it succeeds."
    ),
)

DENY_CONFLICT = FulfillmentAction(
    name="deny",
    label="Deny",
    consequence=(
        "Leaves the newer Batch Request waiting. This snapshot will not be "
        "reconsidered; the conflict can only recur after a material change."
    ),
    destructive=True,
)

RECURRENCE_RULE = (
    "A decision binds to this inventory snapshot alone. Denial or silence "
    "keeps the newer Batch Request waiting, and the conflict may recur only "
    "after a material change to either request or to eligible inventory."
)


def describe_conflict(
    db: Session, conflict: InventoryConflict
) -> dict[str, object]:
    """The Inventory Conflict contract, including its decision scope."""
    snapshot = conflict.inventory_snapshot or {}
    older = db.get(LeadRequest, conflict.older_request_id)
    newer = db.get(LeadRequest, conflict.newer_request_id)

    def side(item: LeadRequest | None, prefix: str) -> dict[str, object]:
        if item is None:
            return {"id": "", "available": False}
        return {
            "id": str(item.id),
            "customerIdentity": item.customer.name,
            "agency": item.customer.agency.name if item.customer.agency else "",
            "leadCount": item.lead_count,
            "states": item.states_snapshot,
            "status": item.status,
            "createdAt": item.created_at,
            "eligibleCount": snapshot.get(f"{prefix}EligibleCount"),
            "available": True,
        }

    return {
        "id": str(conflict.id),
        "status": conflict.status,
        "olderRequest": side(older, "older"),
        "newerRequest": side(newer, "newer"),
        "overlappingLeadCount": len(snapshot.get("sharedLeadIds") or []),
        "snapshotChecksum": conflict.snapshot_checksum,
        "recurrenceRule": RECURRENCE_RULE,
        "decisionBy": conflict.decision_by,
        "decisionReason": conflict.decision_reason,
        "decidedAt": conflict.decided_at,
        "consumedAt": conflict.consumed_at,
        "createdAt": conflict.created_at,
        # Only a pending conflict has a decision left to make; a decided one
        # is idempotent server-side and must not be offered again here.
        "actions": (
            [_describe(CONFIRM_CONFLICT), _describe(DENY_CONFLICT)]
            if conflict.status == "pending"
            else []
        ),
    }


#: Batch Requests that still represent outstanding Fulfillment work.
OPEN_STATUSES: frozenset[str] = frozenset(
    {
        RequestStatus.pending.value,
        RequestStatus.approved.value,
        RequestStatus.waiting_inventory.value,
        RequestStatus.processing.value,
        RequestStatus.generated.value,
        RequestStatus.failed.value,
    }
)


def workspace(db: Session, *, now: datetime | None = None) -> dict[str, object]:
    """The one aggregate the Fulfillment workspace reads."""
    moment = now or datetime.now(timezone.utc)
    open_requests = list(
        db.scalars(
            select(LeadRequest)
            .where(LeadRequest.status.in_(OPEN_STATUSES))
            .order_by(LeadRequest.created_at.desc())
        )
    )
    batch_requests = []
    delivery_failures = []
    for item in open_requests:
        ctx = request_context(db, item, now=moment)
        summary = _summarize_request(item, ctx)
        batch_requests.append(summary)
        artifact = item.artifact
        if item.status == RequestStatus.failed.value and artifact is not None:
            delivery_failures.append(
                {
                    **summary,
                    "lastError": artifact.last_error,
                    "deliveryAttempts": artifact.delivery_attempts,
                    "deliveryStatus": artifact.delivery_status,
                }
            )
    return {
        "batchRequests": batch_requests,
        "inventoryConflicts": [
            describe_conflict(db, conflict)
            for conflict in pending_conflicts(db)
        ],
        "deliveryFailures": delivery_failures,
        "expiredArtifacts": _expired_artifacts(db, now=moment),
        # #58 folded the eligibility controls into the same workspace: a Lead
        # Report and the Batch Request it came from are the same shift's work,
        # and splitting them across screens hid one behind the other.
        "leadReports": report_summaries(db),
        "eligibilityHolds": active_holds(db),
        "suppressedLeads": suppressed_leads(db),
    }


def pending_conflicts(db: Session) -> list[InventoryConflict]:
    """Inventory Conflicts still awaiting their one decision."""
    return list(
        db.scalars(
            select(InventoryConflict)
            .where(InventoryConflict.status == "pending")
            .order_by(InventoryConflict.created_at.desc())
        )
    )


def _expired_artifacts(db: Session, *, now: datetime) -> list[dict[str, object]]:
    """Settled Batch Requests whose Batch Artifact file can be rebuilt.

    Regeneration is the one action that outlives the request's own work: a
    delivered request is finished, but its file expires after 30 days while its
    history stays permanent. Without this the action would exist in the
    contract and never appear anywhere a person could reach it.

    Selected on the indexed ``expires_at`` rather than by stat-ing every
    artifact. A file that goes missing before its expiry is therefore not
    listed here; it is still offered on that request's own detail screen.
    """
    candidates = db.scalars(
        select(LeadRequest)
        .join(BatchArtifact, BatchArtifact.request_id == LeadRequest.id)
        .where(BatchArtifact.expires_at <= now)
        .order_by(LeadRequest.created_at.desc())
    )
    listed = []
    for item in candidates:
        ctx = request_context(db, item, now=now)
        if "regenerate" in available_action_names(ctx):
            listed.append(_summarize_request(item, ctx))
    return listed
