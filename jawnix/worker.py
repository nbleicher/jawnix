from __future__ import annotations

import logging
import time
import uuid

from sqlalchemy import select

from .allocation import allocate_request
from .config import get_settings
from .database import SessionLocal
from .delivery import deliver_request, mark_delivery_failed
from .jobs import claim_next_job, enqueue_job
from .models import Job, JobStatus, LeadRequest, Notification, RequestStatus
from .telegram import TelegramClient
from .transitions import TransitionError, transition_request


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("jawnix.worker")


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


def process_job(job_id: int) -> None:
    settings = get_settings()
    try:
        with SessionLocal.begin() as session:
            job = session.get(Job, job_id)
            if job is None:
                return
            request = session.get(LeadRequest, job.request_id) if job.request_id else None
            telegram = TelegramClient(settings)
            if job.kind in {"notify_request", "update_notification"}:
                if request is None:
                    raise LookupError("Request was not found.")
                _update_notification(session, request, telegram)
            elif job.kind == "telegram_action":
                if request is None:
                    raise LookupError("Request was not found.")
                try:
                    transition_request(session, request.id, str(job.payload.get("action") or ""))
                except TransitionError as exc:
                    log.info("Telegram action for request %s was ignored: %s", request.id, exc.detail)
            elif job.kind == "allocate_request":
                allocate_request(session, uuid.UUID(str(job.request_id)), settings)
            elif job.kind == "deliver_request":
                deliver_request(session, uuid.UUID(str(job.request_id)), settings)
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
            job.status = JobStatus.failed.value
            job.last_error = str(exc)[:4000]
            request = session.get(LeadRequest, job.request_id) if job.request_id else None
            if request is not None and job.kind in {"allocate_request", "deliver_request"}:
                request.status = RequestStatus.failed.value
                request.status_message = (
                    "Batch generation failed; no partial allocation was committed."
                    if job.kind == "allocate_request"
                    else "Email delivery failed. The existing batch is preserved for retry."
                )
                if job.kind == "deliver_request":
                    mark_delivery_failed(session, request.id, str(exc))
                else:
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
