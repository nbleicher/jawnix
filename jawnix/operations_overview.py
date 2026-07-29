"""The actionable administrator Operations overview read contract (#75).

The browser reads one stable aggregate, but each domain source keeps ownership
of its own queue and action rules. A failure in one source is converted into an
unavailable section instead of failing the route or pretending its count is
zero.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .acquisition import (
    nightly_reviews_needing_attention,
    workspace as acquisition_workspace,
)
from .fulfillment import workspace as fulfillment_workspace
from .models import EligibilityHold, Job, JobStatus, LeadReport, NightlyReview


log = logging.getLogger(__name__)

OperationTone = Literal["neutral", "info", "success", "warning", "danger"]
SourceStatus = Literal["available", "unavailable"]
OVERVIEW_ITEM_LIMIT = 12


class ContractModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class OperationAction(ContractModel):
    label: str
    href: str


class OperationItem(ContractModel):
    id: str
    title: str
    summary: str
    status: str
    tone: OperationTone = "neutral"
    next_action: str = Field(alias="nextAction")
    recorded_at: datetime = Field(alias="recordedAt")
    action: OperationAction


class OperationQueue(ContractModel):
    key: str
    title: str
    description: str
    count: int
    items: list[OperationItem]
    empty_title: str = Field(alias="emptyTitle")
    empty_description: str = Field(alias="emptyDescription")


class OperationSource(ContractModel):
    key: str
    title: str
    description: str
    status: SourceStatus
    count: int | None
    queues: list[OperationQueue]
    workspace: OperationAction
    error_title: str | None = Field(default=None, alias="errorTitle")
    error_description: str | None = Field(
        default=None,
        alias="errorDescription",
    )


class OperationsOverview(ContractModel):
    generated_at: datetime = Field(alias="generatedAt")
    available_count: int = Field(alias="availableCount")
    degraded: bool
    sources: list[OperationSource]


def _words(value: object) -> str:
    return str(value or "unknown").replace("_", " ").strip().capitalize()


def _states(item: dict[str, object]) -> str:
    states = item.get("states")
    if not isinstance(states, list) or not states:
        return "No states"
    return ", ".join(str(state) for state in states)


def _first_action(item: dict[str, object], fallback: str) -> str:
    actions = item.get("actions")
    if isinstance(actions, list) and actions:
        first = actions[0]
        if isinstance(first, dict) and first.get("label"):
            return str(first["label"])
    return fallback


def _request_tone(status: str) -> OperationTone:
    return {
        "pending": "warning",
        "approved": "info",
        "processing": "info",
        "waiting_inventory": "warning",
        "generated": "info",
        "failed": "danger",
    }.get(status, "neutral")


def _queue(
    *,
    key: str,
    title: str,
    description: str,
    items: list[OperationItem],
    empty_title: str,
    empty_description: str,
    count: int | None = None,
) -> OperationQueue:
    return OperationQueue(
        key=key,
        title=title,
        description=description,
        count=len(items) if count is None else count,
        items=items,
        empty_title=empty_title,
        empty_description=empty_description,
    )


def read_fulfillment_source(db: Session) -> OperationSource:
    """Adapt the existing Fulfillment aggregate without re-deriving actions."""
    data = fulfillment_workspace(db)
    delivery_failures = list(data["deliveryFailures"])
    delivery_failure_ids = {str(item["id"]) for item in delivery_failures}

    batch_requests = [
        item
        for item in data["batchRequests"]
        if str(item["id"]) not in delivery_failure_ids
    ]
    request_items = [
        OperationItem(
            id=str(item["id"]),
            title=str(item.get("customerIdentity") or "Customer"),
            summary=(
                f"{int(item.get('leadCount') or 0):,} Leads · "
                f"{_states(item)}. "
                f"{str(item.get('statusMessage') or '').strip()}"
            ).strip(),
            status=_words(item.get("status")),
            tone=_request_tone(str(item.get("status") or "")),
            next_action=_first_action(item, "Review Batch Request"),
            recorded_at=item["createdAt"],
            action=OperationAction(
                label="Open Batch Request",
                href=f"/app/admin/fulfillment/requests/{item['id']}",
            ),
        )
        for item in batch_requests[:OVERVIEW_ITEM_LIMIT]
    ]

    conflict_items = []
    for item in data["inventoryConflicts"][:OVERVIEW_ITEM_LIMIT]:
        older = item.get("olderRequest") or {}
        newer = item.get("newerRequest") or {}
        conflict_items.append(
            OperationItem(
                id=str(item["id"]),
                title=(
                    f"{older.get('customerIdentity') or 'Older request'} ↔ "
                    f"{newer.get('customerIdentity') or 'Newer request'}"
                ),
                summary=(
                    f"{int(item.get('overlappingLeadCount') or 0):,} "
                    "overlapping eligible Leads in this inventory snapshot."
                ),
                status="Awaiting decision",
                tone="warning",
                next_action=_first_action(
                    item,
                    "Confirm once or deny",
                ),
                recorded_at=item["createdAt"],
                action=OperationAction(
                    label="Decide Inventory Conflict",
                    href=(
                        "/app/admin/fulfillment/conflicts/"
                        f"{item['id']}"
                    ),
                ),
            )
        )

    report_items = [
        OperationItem(
            id=str(item["id"]),
            title=str(item.get("reasonLabel") or "Lead Report"),
            summary=(
                f"Filed by "
                f"{(item.get('customer') or {}).get('name') or 'a Customer'}."
                + (
                    " Eligibility Hold active."
                    if item.get("eligibilityHeld")
                    else " No Eligibility Hold."
                )
            ),
            status="Open",
            tone="warning",
            next_action="Resolve Lead Report",
            recorded_at=item["createdAt"],
            action=OperationAction(
                label="Review Lead Report",
                href=str(item["href"]),
            ),
        )
        for item in data["leadReports"][:OVERVIEW_ITEM_LIMIT]
    ]

    hold_items = [
        OperationItem(
            id=str(item["id"]),
            title=(
                f"Lead {item.get('leadPhone') or item.get('leadId')}"
            ),
            summary=(
                f"{item.get('reasonLabel') or _words(item.get('reason'))}. "
                "Only resolving the related Lead Report releases this hold."
            ),
            status="Eligibility held",
            tone="danger",
            next_action="Resolve Lead Report",
            recorded_at=item["createdAt"],
            action=OperationAction(
                label="Review Eligibility Hold",
                href=str(item["href"]),
            ),
        )
        for item in data["eligibilityHolds"][:OVERVIEW_ITEM_LIMIT]
    ]

    delivery_items = [
        OperationItem(
            id=str(item["id"]),
            title=str(item.get("customerIdentity") or "Customer"),
            summary=(
                str(item.get("lastError") or "Delivery failed.")
                + f" {int(item.get('deliveryAttempts') or 0):,} "
                "delivery attempt"
                + (
                    ""
                    if int(item.get("deliveryAttempts") or 0) == 1
                    else "s"
                )
                + "."
            ),
            status="Delivery failed",
            tone="danger",
            next_action=_first_action(item, "Review delivery recovery"),
            recorded_at=item["createdAt"],
            action=OperationAction(
                label="Recover Delivery",
                href=f"/app/admin/fulfillment/requests/{item['id']}",
            ),
        )
        for item in delivery_failures[:OVERVIEW_ITEM_LIMIT]
    ]

    open_report_count = int(
        db.scalar(
            select(func.count())
            .select_from(LeadReport)
            .where(
                LeadReport.status == "open"
            )
        )
        or 0
    )
    active_hold_count = int(
        db.scalar(
            select(func.count())
            .select_from(EligibilityHold)
            .where(
                EligibilityHold.active.is_(True)
            )
        )
        or 0
    )

    queues = [
        _queue(
            key="batchRequests",
            title="Pending Batch Requests",
            description=(
                "Outstanding requests whose next valid Fulfillment action is "
                "projected by the server."
            ),
            items=request_items,
            count=len(batch_requests),
            empty_title="No Batch Requests need attention",
            empty_description=(
                "New Customer requests and recoverable generation failures "
                "will appear here."
            ),
        ),
        _queue(
            key="inventoryConflicts",
            title="Inventory Conflicts",
            description=(
                "One decision authorizes one attempt against one inventory "
                "snapshot."
            ),
            items=conflict_items,
            count=len(data["inventoryConflicts"]),
            empty_title="No Inventory Conflicts are waiting",
            empty_description=(
                "No newer request is waiting on an overlapping-inventory "
                "decision."
            ),
        ),
        _queue(
            key="leadReports",
            title="Lead Reports",
            description=(
                "Immutable Customer evidence awaiting an administrator "
                "decision."
            ),
            items=report_items,
            count=open_report_count,
            empty_title="No Lead Reports are open",
            empty_description=(
                "New reports appear here without changing what was delivered."
            ),
        ),
        _queue(
            key="eligibilityHolds",
            title="Eligibility Holds",
            description=(
                "Leads withheld from allocation until the related report is "
                "resolved."
            ),
            items=hold_items,
            count=active_hold_count,
            empty_title="No Eligibility Holds are active",
            empty_description=(
                "No open report is currently withholding a Lead."
            ),
        ),
        _queue(
            key="deliveryFailures",
            title="Delivery failures",
            description=(
                "Generated batches that did not reach their Customer."
            ),
            items=delivery_items,
            count=len(delivery_failures),
            empty_title="No delivery failures",
            empty_description=(
                "Every generated batch currently has a successful delivery."
            ),
        ),
    ]
    return OperationSource(
        key="fulfillment",
        title="Fulfillment",
        description=(
            "Approval, allocation, eligibility, and delivery recovery work."
        ),
        status="available",
        count=sum(queue.count for queue in queues),
        queues=queues,
        workspace=OperationAction(
            label="Open Fulfillment",
            href="/app/admin/fulfillment",
        ),
    )


def _job_destination(
    job: Job,
    db: Session,
    seen: set[int] | None = None,
) -> OperationAction:
    visited = set() if seen is None else seen
    if job.id in visited:
        return OperationAction(
            label="Open owning workspace",
            href="/app/admin/fulfillment",
        )
    visited.add(job.id)
    if job.request_id is not None:
        return OperationAction(
            label="Open Batch Request",
            href=f"/app/admin/fulfillment/requests/{job.request_id}",
        )
    payload = job.payload or {}
    if failed_job_id := payload.get("failed_job_id"):
        try:
            original_job_id = int(failed_job_id)
        except (TypeError, ValueError):
            original_job_id = 0
        original = db.get(Job, original_job_id) if original_job_id else None
        if original is not None:
            return _job_destination(original, db, visited)
    if conflict_id := payload.get("conflict_id"):
        return OperationAction(
            label="Open Inventory Conflict",
            href=f"/app/admin/fulfillment/conflicts/{conflict_id}",
        )
    if report_id := payload.get("report_id"):
        return OperationAction(
            label="Open Lead Report",
            href=f"/app/admin/fulfillment/reports/{report_id}",
        )
    if configuration_id := payload.get("configuration_id"):
        return OperationAction(
            label="Open Scraper Configuration",
            href=(
                "/app/admin/acquisition/configurations/"
                f"{configuration_id}"
            ),
        )
    if review_id := payload.get("review_id"):
        try:
            parsed_review_id = uuid.UUID(str(review_id))
        except (AttributeError, TypeError, ValueError):
            parsed_review_id = None
        review = (
            db.get(NightlyReview, parsed_review_id)
            if parsed_review_id is not None
            else None
        )
        if review is not None and review.scraper_run_id is not None:
            return OperationAction(
                label="Open related Scrape Run",
                href=(
                    "/app/admin/acquisition/runs/"
                    f"{review.scraper_run_id}"
                ),
            )
    if payload.get("anomaly_id") or payload.get("recommendation_id"):
        return OperationAction(
            label="Open Acquisition",
            href="/app/admin/acquisition",
        )
    return OperationAction(
        label="Open owning workspace",
        href=(
            "/app/admin/acquisition"
            if job.kind
            in {
                "run_scraper",
                "sync_inventory",
                "notify_nightly_review",
                "update_nightly_review_notification",
                "notify_scrape_anomaly",
                "update_scrape_anomaly_notification",
                "telegram_anomaly_action",
                "telegram_recommendation_action",
            }
            else "/app/admin/fulfillment"
        ),
    )


def read_jobs_source(db: Session) -> OperationSource:
    failed_count = int(
        db.scalar(
            select(func.count(Job.id)).where(
                Job.status == JobStatus.failed.value
            )
        )
        or 0
    )
    jobs = list(
        db.scalars(
            select(Job)
            .where(Job.status == JobStatus.failed.value)
            .order_by(Job.created_at.desc(), Job.id.desc())
            .limit(25)
        )
    )
    items = [
        OperationItem(
            id=str(job.id),
            title=f"{_words(job.kind)} · Job {job.id}",
            summary=(
                f"Failed after {job.attempts:,} attempt"
                f"{'' if job.attempts == 1 else 's'}. "
                "Open the affected record to verify its current state before "
                "trying a domain action again."
            ),
            status="Failed",
            tone="danger",
            next_action="Review affected record",
            recorded_at=job.created_at,
            action=_job_destination(job, db),
        )
        for job in jobs
    ]
    queue = _queue(
        key="failedJobs",
        title="Failed jobs",
        description=(
            "Background work that stopped without completing its requested "
            "domain action."
        ),
        items=items,
        count=failed_count,
        empty_title="No background jobs have failed",
        empty_description=(
            "Failed worker activity will appear here with a link to the "
            "affected record or owning workspace."
        ),
    )
    return OperationSource(
        key="backgroundJobs",
        title="Background jobs",
        description=(
            "Worker failures that need their affected record checked before "
            "recovery."
        ),
        status="available",
        count=queue.count,
        queues=[queue],
        workspace=OperationAction(
            label="Review Activity",
            href="/app/admin/activity",
        ),
    )


def read_acquisition_source(db: Session) -> OperationSource:
    """Adapt Jawnix-owned acquisition records; no live scale read is added."""
    data = acquisition_workspace(db)
    held = [
        item
        for item in data["scrapeAnomalies"]
        if item.get("decidable")
    ]
    anomaly_items = [
        OperationItem(
            id=str(item["id"]),
            title=f"Scrape Run {item['scraperRunId']}",
            summary=(
                f"{len(item.get('anomalousSegments') or []):,} anomalous "
                "Source Segment"
                + (
                    ""
                    if len(item.get("anomalousSegments") or []) == 1
                    else "s"
                )
                + " held before publication."
            ),
            status="Awaiting decision",
            tone="warning",
            next_action="Confirm or deny held output",
            recorded_at=item["createdAt"],
            action=OperationAction(
                label="Review Scrape Anomaly",
                href="/app/admin/acquisition#held-scrape-anomalies",
            ),
        )
        for item in held[:OVERVIEW_ITEM_LIMIT]
    ]

    attention_reviews = nightly_reviews_needing_attention(db)
    review_items = []
    for item in attention_reviews[:OVERVIEW_ITEM_LIMIT]:
        failures = (
            item.get("summary", {}).get("failures", [])
            if isinstance(item.get("summary"), dict)
            else []
        )
        if item.get("reconcilable"):
            next_action = "Review delivery evidence"
            status = "Delivery unknown"
        elif item.get("status") != "complete":
            next_action = "Review delayed Nightly Review"
            status = _words(item.get("status"))
        else:
            next_action = "Review recorded failures"
            status = f"{len(failures):,} recorded failure"
            if len(failures) != 1:
                status += "s"
        review_items.append(
            OperationItem(
                id=str(item["id"]),
                title=str(
                    item.get("reviewDate")
                    or f"Nightly Review {str(item['id'])[:8]}"
                ),
                summary=(
                    str(
                        item.get("telegramDeliveryError")
                        or "This Nightly Review needs operator attention."
                    )
                ),
                status=status,
                tone="danger" if failures else "warning",
                next_action=next_action,
                recorded_at=item["createdAt"],
                action=OperationAction(
                    label="Review Nightly Review",
                    href="/app/admin/acquisition#nightly-reviews",
                ),
            )
        )

    queues = [
        _queue(
            key="scrapeAnomalies",
            title="Scrape Anomalies",
            description=(
                "Staged output held until an administrator confirms or "
                "denies it."
            ),
            items=anomaly_items,
            count=len(held),
            empty_title="No Scrape Anomalies are held",
            empty_description=(
                "No staged Scraper Dataset is waiting on an anomaly decision."
            ),
        ),
        _queue(
            key="nightlyReviews",
            title="Nightly Reviews",
            description=(
                "Durable nightly summaries with delayed work, failed evidence, "
                "or unknown delivery."
            ),
            items=review_items,
            count=len(attention_reviews),
            empty_title="No Nightly Reviews need attention",
            empty_description=(
                "Recent Nightly Reviews completed without an operator recovery "
                "step."
            ),
        ),
    ]
    return OperationSource(
        key="acquisition",
        title="Acquisition",
        description=(
            "Held Scraper publication decisions and Nightly Review recovery."
        ),
        status="available",
        count=sum(queue.count for queue in queues),
        queues=queues,
        workspace=OperationAction(
            label="Open Acquisition",
            href="/app/admin/acquisition",
        ),
    )


def _unavailable_source(
    *,
    key: str,
    title: str,
    description: str,
    workspace: OperationAction,
) -> OperationSource:
    return OperationSource(
        key=key,
        title=title,
        description=description,
        status="unavailable",
        count=None,
        queues=[],
        workspace=workspace,
        error_title=f"{title} work is temporarily unavailable",
        error_description=(
            "Only this section could not be refreshed. The other Operations "
            "sections remain usable; retry, then review Activity if this "
            "continues."
        ),
    )


def operations_overview(db: Session) -> OperationsOverview:
    """Read each source in isolation and preserve every successful section."""
    definitions: tuple[
        tuple[
            str,
            str,
            str,
            OperationAction,
            Callable[[Session], OperationSource],
        ],
        ...,
    ] = (
        (
            "fulfillment",
            "Fulfillment",
            "Approval, allocation, eligibility, and delivery recovery work.",
            OperationAction(
                label="Open Fulfillment",
                href="/app/admin/fulfillment",
            ),
            read_fulfillment_source,
        ),
        (
            "backgroundJobs",
            "Background jobs",
            "Worker failures whose affected records need checking.",
            OperationAction(
                label="Review Activity",
                href="/app/admin/activity",
            ),
            read_jobs_source,
        ),
        (
            "acquisition",
            "Acquisition",
            "Held publication decisions and Nightly Review recovery.",
            OperationAction(
                label="Open Acquisition",
                href="/app/admin/acquisition",
            ),
            read_acquisition_source,
        ),
    )
    sources = []
    for key, title, description, workspace, reader in definitions:
        try:
            # A savepoint keeps a source-level query failure from poisoning the
            # rest of this read transaction on PostgreSQL.
            with db.begin_nested():
                source = reader(db)
        except Exception:
            log.exception("Operations overview source %s failed", key)
            source = _unavailable_source(
                key=key,
                title=title,
                description=description,
                workspace=workspace,
            )
        sources.append(source)
    return OperationsOverview(
        generated_at=datetime.now(timezone.utc),
        available_count=sum(
            source.count or 0
            for source in sources
            if source.status == "available"
        ),
        degraded=any(
            source.status == "unavailable"
            for source in sources
        ),
        sources=sources,
    )
