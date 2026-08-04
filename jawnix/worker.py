from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from .allocation import allocate_request, fulfill_round_robin
from .config import get_settings
from .database import SessionLocal
from .delivery import deliver_request, mark_delivery_failed
from .jobs import claim_next_job, enqueue_job
from .metrics_emit import (
    EMIT_LEAD_ASSIGNED_JOB,
    EMIT_MAX_ATTEMPTS,
    MetricsEmitTransientError,
    emit_lead_assigned,
    emit_retry_delay,
)
from .milestone_emails import (
    MILESTONE_EMAIL_JOB,
    enqueue_milestone_email,
    send_milestone_email,
)
from .models import (
    Job,
    JobStatus,
    InventoryConflict,
    LeadRequest,
    NightlyReview,
    Notification,
    RequestStatus,
    ScrapeAnomaly,
    ScraperRun,
    SourceRecommendation,
    utcnow,
)
from .telegram import TelegramClient
from .transitions import TransitionError, transition_request


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("jawnix.worker")

TELEGRAM_DECISION_JOB_KINDS = frozenset(
    {
        "telegram_action",
        "telegram_anomaly_action",
        "telegram_inventory_conflict_action",
        "telegram_recommendation_action",
        "telegram_exclusion_action",
    }
)


def _telegram_action_label(job: Job) -> str:
    target = {
        "telegram_action": "Batch Request",
        "telegram_anomaly_action": "Scrape Anomaly",
        "telegram_inventory_conflict_action": "Inventory Conflict",
        "telegram_recommendation_action": "Source Recommendation",
        "telegram_exclusion_action": "Exclusion List",
    }.get(job.kind, "Jawnix")
    action = str(job.payload.get("action") or "requested").replace("_", " ")
    return f"{target} {action}"


def _enqueue_telegram_action_failure(session, job: Job) -> None:
    """Durably report a failed Telegram decision without leaking its exception.

    This is transport recovery only. It does not decide, retry, or mutate the
    target record; the same domain command remains the sole decision path.
    Stale and duplicate actions are successful no-ops and never reach here.
    """
    if job.kind not in TELEGRAM_DECISION_JOB_KINDS:
        return
    if job.payload.get("failure_notification_job_id"):
        return
    notification = enqueue_job(
        session,
        "notify_telegram_action_failure",
        payload={
            "failed_job_id": job.id,
            "message": (
                f"Jawnix could not complete the Telegram "
                f"{_telegram_action_label(job)} action. "
                "The record was not changed. Open Jawnix to review its "
                "current state before trying again."
            ),
        },
    )
    # A manually retried failed job must not fan out another failure message.
    # Assign a new dict so SQLAlchemy's JSON column sees the mutation.
    job.payload = {
        **job.payload,
        "failure_notification_job_id": notification.id,
    }


def _update_notification(session, request: LeadRequest, telegram: TelegramClient) -> None:
    notification = session.scalar(select(Notification).where(Notification.request_id == request.id))
    if notification is None:
        chat_id, message_id = telegram.post_request(request)
        session.add(
            Notification(
                request_id=request.id,
                provider="telegram",
                destination_id=chat_id,
                message_id=message_id,
            )
        )
    else:
        telegram.update_request(request, notification.destination_id, notification.message_id)


def _process_initial_nightly_review_notification(
    job_id: int,
    settings,
) -> None:
    with SessionLocal.begin() as session:
        job = session.get(Job, job_id)
        if job is None:
            return
        review = session.scalar(
            select(NightlyReview)
            .where(
                NightlyReview.id
                == uuid.UUID(str(job.payload.get("review_id")))
            )
            .with_for_update()
        )
        if review is None:
            job.status = JobStatus.failed.value
            job.last_error = "Nightly Review was not found."
            return
        if review.telegram_message_id:
            review.telegram_delivery_state = "sent"
            review.telegram_delivery_error = ""
            job.status = JobStatus.complete.value
            job.last_error = ""
            return
        if review.telegram_delivery_state in {"sending", "unknown"}:
            review.telegram_delivery_state = "unknown"
            review.telegram_delivery_error = (
                "Telegram delivery outcome is unknown after a worker "
                "interruption; operator reconciliation is required."
            )
            job.status = JobStatus.failed.value
            job.last_error = review.telegram_delivery_error
            return
        review.telegram_delivery_state = "sending"
        review.telegram_delivery_started_at = datetime.now(timezone.utc)
        review.telegram_delivery_error = ""
        review_id = review.id
        scraper_run_id = review.scraper_run_id

    try:
        with SessionLocal() as session:
            review = session.get(NightlyReview, review_id)
            anomaly = session.scalar(
                select(ScrapeAnomaly).where(
                    ScrapeAnomaly.scraper_run_id == scraper_run_id
                )
            )
            message_id = TelegramClient(settings).post_nightly_review(
                review,
                anomaly,
            )
    except Exception as exc:
        log.exception(
            "Nightly Review Telegram delivery outcome is unknown"
        )
        with SessionLocal.begin() as session:
            review = session.get(NightlyReview, review_id)
            job = session.get(Job, job_id)
            if review is not None:
                review.telegram_delivery_state = "unknown"
                review.telegram_delivery_error = str(exc)[:4000]
            if job is not None:
                job.status = JobStatus.failed.value
                job.last_error = (
                    "Telegram delivery outcome is unknown; operator "
                    "reconciliation is required. "
                    + str(exc)
                )[:4000]
        return

    with SessionLocal.begin() as session:
        review = session.get(NightlyReview, review_id)
        job = session.get(Job, job_id)
        if review is None or job is None:
            return
        review.telegram_message_id = message_id
        review.telegram_delivery_state = "sent"
        review.telegram_delivery_error = ""
        anomaly = session.scalar(
            select(ScrapeAnomaly).where(
                ScrapeAnomaly.scraper_run_id == scraper_run_id
            )
        )
        if anomaly is not None:
            anomaly.telegram_chat_id = settings.telegram_chat_id
            anomaly.telegram_message_id = message_id
        job.status = JobStatus.complete.value
        job.last_error = ""


def _process_telegram_action_failure_notification(
    job_id: int,
    settings,
) -> None:
    """Send one failure notice without retrying an unknown Telegram outcome."""
    with SessionLocal.begin() as session:
        job = session.get(Job, job_id)
        if job is None:
            return
        state = str(job.payload.get("delivery_state") or "pending")
        if state == "sent" or job.payload.get("message_id"):
            job.status = JobStatus.complete.value
            job.last_error = ""
            return
        if state in {"sending", "unknown"}:
            job.payload = {
                **job.payload,
                "delivery_state": "unknown",
            }
            job.status = JobStatus.failed.value
            job.last_error = (
                "Telegram failure-notification delivery outcome is unknown; "
                "it was not sent again."
            )
            return
        job.payload = {
            **job.payload,
            "delivery_state": "sending",
        }
        message = str(job.payload["message"])

    try:
        message_id = TelegramClient(settings).post_action_failure(message)
    except Exception as exc:
        log.exception("Telegram action-failure notification failed")
        with SessionLocal.begin() as session:
            job = session.get(Job, job_id)
            if job is None:
                return
            job.payload = {
                **job.payload,
                "delivery_state": "failed",
            }
            job.status = JobStatus.failed.value
            job.last_error = str(exc)[:4000]
        return

    with SessionLocal.begin() as session:
        job = session.get(Job, job_id)
        if job is None:
            return
        job.payload = {
            **job.payload,
            "delivery_state": "sent",
            "message_id": message_id,
        }
        job.status = JobStatus.complete.value
        job.last_error = ""


def process_job(job_id: int) -> None:
    settings = get_settings()
    with SessionLocal() as session:
        job_kind = session.scalar(
            select(Job.kind).where(Job.id == job_id)
        )
    if job_kind == "notify_nightly_review":
        _process_initial_nightly_review_notification(
            job_id,
            settings,
        )
        return
    if job_kind == "notify_telegram_action_failure":
        _process_telegram_action_failure_notification(
            job_id,
            settings,
        )
        return
    try:
        with SessionLocal.begin() as session:
            job = session.get(Job, job_id)
            if job is None:
                return
            request = session.get(LeadRequest, job.request_id) if job.request_id else None
            telegram = TelegramClient(settings)
            if job.kind in {
                "notify_request",
                "update_notification",
                "licensed_states_changed",
            }:
                if request is None:
                    raise LookupError("Request was not found.")
                _update_notification(session, request, telegram)
            elif job.kind == "telegram_action":
                if request is None:
                    raise LookupError("Request was not found.")
                try:
                    transition_request(
                        session,
                        request.id,
                        str(job.payload.get("action") or ""),
                        actor_id=(
                            "telegram:"
                            + str(job.payload["approver_user_id"])
                        ),
                        reason="Telegram Batch Request decision",
                    )
                except TransitionError as exc:
                    log.info("Telegram action for request %s was ignored: %s", request.id, exc.detail)
                    # A Jawnix action may have won the row lock before this
                    # callback. Refresh from the durable state even when the
                    # clicked action is now stale, so the old keyboard cannot
                    # remain an apparently live, divergent action surface.
                    enqueue_job(session, "update_notification", request.id)
            elif job.kind == "allocate_request":
                allocate_request(session, uuid.UUID(str(job.request_id)), settings)
            elif job.kind == "fulfill_round_robin":
                fulfill_round_robin(session, settings)
            elif job.kind == "update_nightly_review_notification":
                review = session.scalar(
                    select(NightlyReview)
                    .where(
                        NightlyReview.id
                        == uuid.UUID(
                            str(job.payload.get("review_id"))
                        )
                    )
                    .with_for_update()
                )
                if review is None:
                    raise LookupError("Nightly Review was not found.")
                anomaly = session.scalar(
                    select(ScrapeAnomaly).where(
                        ScrapeAnomaly.scraper_run_id
                        == review.scraper_run_id
                    )
                )
                if not review.telegram_message_id:
                    raise RuntimeError(
                        "A Nightly Review cannot be updated before its "
                        "Telegram delivery is reconciled."
                    )
                telegram.update_nightly_review(review, anomaly)
                if anomaly is not None:
                    anomaly.telegram_chat_id = settings.telegram_chat_id
                    anomaly.telegram_message_id = (
                        review.telegram_message_id
                    )
            elif job.kind == "run_scraper":
                from jawnix_data.scraper import run_scrape

                result = run_scrape(
                    session,
                    settings,
                    uuid.UUID(str(job.payload["configuration_id"])),
                    manual=bool(job.payload.get("manual")),
                )
                if result["status"] == "failed":
                    log.error(
                        "Scrape Run %s failed: %s",
                        result.get("runId"),
                        result.get("error"),
                    )
            elif job.kind == "sync_inventory":
                from jawnix_data.scraper import sync_dataset_version

                sync_dataset_version(
                    session,
                    settings,
                    int(job.payload["dataset_version"]),
                )
            elif job.kind == "ingest_exclusion_list":
                from .exclusions import ingest_exclusion_list

                ingest_exclusion_list(
                    session,
                    uuid.UUID(str(job.payload["exclusion_list_id"])),
                )
            elif job.kind == "ingest_niche_assignments":
                from .niche_policy import ingest_niche_assignments

                ingest_niche_assignments(
                    session,
                    uuid.UUID(
                        str(job.payload["niche_assignment_upload_id"])
                    ),
                )
            elif job.kind in {
                "notify_scrape_anomaly",
                "update_scrape_anomaly_notification",
            }:
                anomaly = session.get(
                    ScrapeAnomaly,
                    uuid.UUID(str(job.payload["anomaly_id"])),
                )
                if anomaly is None:
                    raise LookupError("Scrape Anomaly was not found.")
                run = session.get(ScraperRun, anomaly.scraper_run_id)
                if run is None:
                    raise LookupError("Scrape Run was not found.")
                if not anomaly.telegram_message_id:
                    (
                        anomaly.telegram_chat_id,
                        anomaly.telegram_message_id,
                    ) = telegram.post_scrape_anomaly(anomaly, run)
                else:
                    telegram.update_scrape_anomaly(anomaly, run)
            elif job.kind == "telegram_anomaly_action":
                from jawnix_data.scraper import decide_scrape_anomaly

                decide_scrape_anomaly(
                    session,
                    settings,
                    uuid.UUID(str(job.payload["anomaly_id"])),
                    action=str(job.payload["action"]),
                    actor_id=(
                        "telegram:"
                        + str(job.payload["approver_user_id"])
                    ),
                    reason="Telegram Scrape Anomaly decision",
                )
            elif job.kind in {
                "notify_inventory_conflict",
                "update_inventory_conflict_notification",
            }:
                conflict = session.get(
                    InventoryConflict,
                    uuid.UUID(str(job.payload["conflict_id"])),
                )
                if conflict is None:
                    raise LookupError("Inventory Conflict was not found.")
                if not conflict.telegram_message_id:
                    (
                        conflict.telegram_chat_id,
                        conflict.telegram_message_id,
                    ) = telegram.post_inventory_conflict(conflict)
                else:
                    telegram.update_inventory_conflict(conflict)
            elif job.kind == "telegram_inventory_conflict_action":
                from .allocation import decide_inventory_conflict

                decide_inventory_conflict(
                    session,
                    uuid.UUID(str(job.payload["conflict_id"])),
                    action=str(job.payload["action"]),
                    actor_id=(
                        "telegram:"
                        + str(job.payload["approver_user_id"])
                    ),
                    reason="Telegram Inventory Conflict decision",
                    )
            elif job.kind == "telegram_recommendation_action":
                from .recommendations import (
                    RecommendationDecisionError,
                    decide_recommendation,
                )

                try:
                    recommendation_id = uuid.UUID(
                        str(job.payload["recommendation_id"])
                    )
                    bound = session.get(
                        SourceRecommendation,
                        recommendation_id,
                    )
                    if bound is None:
                        raise RecommendationDecisionError(
                            "Source Recommendation was not found."
                        )
                    if (
                        job.payload.get("configuration_version")
                        != bound.configuration_version
                    ):
                        raise RecommendationDecisionError(
                            "Telegram callback evidence is stale."
                        )
                    recommendation = decide_recommendation(
                        session,
                        recommendation_id,
                        str(job.payload["action"]),
                        actor_id=(
                            "telegram:"
                            + str(job.payload["approver_user_id"])
                        ),
                        reason="Telegram recommendation decision",
                        apply_enabled=(
                            settings.recommendation_apply_enabled
                        ),
                        shadow_mode=settings.recommendation_shadow_mode,
                    )
                    if recommendation.nightly_review_id:
                        enqueue_job(
                            session,
                            "update_nightly_review_notification",
                            payload={
                                "review_id": str(
                                    recommendation.nightly_review_id
                                )
                            },
                        )
                except RecommendationDecisionError as exc:
                    log.info(
                        "Telegram recommendation decision ignored: %s",
                        exc,
                    )
            elif job.kind == "telegram_exclusion_action":
                from .exclusions import (
                    ExclusionDecisionError,
                    decide_exclusion_list,
                )

                try:
                    decide_exclusion_list(
                        session,
                        uuid.UUID(str(job.payload["exclusion_list_id"])),
                        str(job.payload["action"]),
                        actor_id=(
                            "telegram:"
                            + str(job.payload["approver_user_id"])
                        ),
                        reason="Telegram Exclusion List decision",
                    )
                except ExclusionDecisionError as exc:
                    log.info(
                        "Telegram exclusion decision ignored: %s",
                        exc,
                    )
            elif job.kind == "deliver_request":
                deliver_request(session, uuid.UUID(str(job.request_id)), settings)
            elif job.kind == EMIT_LEAD_ASSIGNED_JOB:
                emit_lead_assigned(
                    session,
                    uuid.UUID(str(job.request_id)),
                    settings,
                )
            elif job.kind == MILESTONE_EMAIL_JOB:
                if request is None:
                    raise LookupError("Request was not found.")
                if job.status == JobStatus.complete.value:
                    return
                milestone = str(job.payload.get("milestone") or "")
                message_id = send_milestone_email(
                    request,
                    milestone,
                    settings,
                )
                job.payload = {
                    **job.payload,
                    "message_id": message_id,
                }
            else:
                raise ValueError(f"Unknown job kind: {job.kind}")
            job.status = JobStatus.complete.value
            job.last_error = ""
    except Exception as exc:
        # The processing transaction has rolled back here, so allocation and
        # artifact state can never be partially committed. Record the failure
        # in a new transaction and preserve any previously generated artifact.
        log.exception("Job %s failed", job_id)
        with SessionLocal.begin() as session:
            job = session.get(Job, job_id)
            if job is None:
                return
            if (
                job.kind == EMIT_LEAD_ASSIGNED_JOB
                and isinstance(exc, MetricsEmitTransientError)
                and job.attempts < EMIT_MAX_ATTEMPTS
            ):
                # Transient metrics-ingest failure (network / 5xx): reschedule
                # with backoff instead of failing permanently. Metrics dedups
                # on distribution_event.id, so re-POSTing is safe. Only after
                # EMIT_MAX_ATTEMPTS does the job fail for good — and even then
                # it never touches the customer request's status.
                job.status = JobStatus.queued.value
                job.run_after = utcnow() + emit_retry_delay(job.attempts)
                job.locked_at = None
                job.locked_by = ""
                job.last_error = str(exc)[:4000]
                return
            job.status = JobStatus.failed.value
            job.last_error = str(exc)[:4000]
            _enqueue_telegram_action_failure(session, job)
            request = session.get(LeadRequest, job.request_id) if job.request_id else None
            if request is not None and job.kind in {"allocate_request", "deliver_request"}:
                request.status = RequestStatus.failed.value
                request.status_message = (
                    "Batch generation failed; no partial allocation was committed."
                    if job.kind == "allocate_request"
                    else (
                        "Portal notification failed. The existing Batch "
                        "Artifact is preserved for retry."
                    )
                )
                request.closed_at = utcnow()
                if job.kind == "deliver_request":
                    mark_delivery_failed(session, request.id, str(exc))
                else:
                    enqueue_milestone_email(session, request)
                    enqueue_job(session, "update_notification", request.id)


def run() -> None:
    settings = get_settings()
    log.info("Worker %s started", settings.worker_id)
    while True:
        job_id = None
        with SessionLocal.begin() as session:
            job = claim_next_job(session, settings.worker_id, settings.job_lock_timeout_seconds)
            if job:
                job_id = job.id
        if job_id is None:
            time.sleep(settings.worker_poll_seconds)
            continue
        process_job(job_id)


if __name__ == "__main__":
    run()
