from __future__ import annotations

import hashlib
from fastapi import (
    BackgroundTasks,
    Depends,
    HTTPException,
    Request,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from svix.webhooks import Webhook, WebhookVerificationError
from ..config import Settings, get_settings
from ..database import get_db
from ..jobs import enqueue_job
from ..milestone_emails import enqueue_milestone_email
from ..models import (
    BatchArtifact,
    LeadRequest,
    RequestStatus,
    ScraperConfiguration,
    ScrapeAnomaly,
    WebhookReceipt,
    utcnow,
)
from ..telegram import (
    TelegramClient,
    parse_anomaly_callback_data,
    parse_callback_data,
    parse_conflict_callback_data,
    parse_exclusion_callback_data,
    parse_recommendation_callback_data,
    verify_telegram_secret,
)
from fastapi import APIRouter


router = APIRouter()


@router.post("/api/integrations/telegram/webhook")
async def telegram_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    if not verify_telegram_secret(
        request.headers.get("X-Telegram-Bot-Api-Secret-Token", ""),
        settings.telegram_webhook_secret,
    ):
        raise HTTPException(status_code=401, detail="Invalid Telegram webhook secret.")
    try:
        payload = await request.json()
        update_id = str(payload["update_id"])
        callback = payload.get("callback_query")
    except (KeyError, ValueError):
        raise HTTPException(status_code=400, detail="Malformed Telegram update.") from None
    if not callback:
        return {"ok": True, "ignored": True}
    try:
        callback_query_id = str(callback["id"])
        user_id = str(callback["from"]["id"])
        chat_id = str(callback["message"]["chat"]["id"])
        callback_value = str(callback["data"])
    except (KeyError, ValueError):
        raise HTTPException(status_code=400, detail="Malformed Telegram callback.") from None

    target_kind = "request"
    try:
        action, target_id = parse_callback_data(callback_value)
    except ValueError:
        try:
            (
                action,
                target_id,
                target_configuration_version,
                target_dataset_checksum,
            ) = parse_anomaly_callback_data(callback_value)
            target_kind = "anomaly"
        except ValueError:
            try:
                action, target_id = parse_conflict_callback_data(
                    callback_value
                )
                target_kind = "conflict"
            except ValueError:
                try:
                    action, target_id = parse_exclusion_callback_data(
                        callback_value
                    )
                    target_kind = "exclusion"
                except ValueError:
                    try:
                        (
                            action,
                            target_id,
                            target_configuration_version,
                            target_evidence_checksum,
                        ) = parse_recommendation_callback_data(
                            callback_value
                        )
                        target_kind = "recommendation"
                    except ValueError:
                        raise HTTPException(
                            status_code=400,
                            detail="Malformed Telegram callback.",
                        ) from None

    telegram = TelegramClient(settings)
    if user_id not in settings.telegram_approvers or chat_id != settings.telegram_chat_id:
        background_tasks.add_task(telegram.answer_callback, callback_query_id, "Not authorized")
        return {"ok": True, "ignored": True}
    if target_kind == "anomaly":
        # A button carries the dataset checksum and Scraper Configuration
        # version it was rendered against. Rejecting a mismatch here, before
        # the durable Job even exists, keeps a stale keyboard (an old
        # message, a superseded configuration) from queuing a decision that
        # decide_scrape_anomaly's own staged-file checksum check cannot see
        # coming, because by then the anomaly row may already have moved on.
        anomaly = db.get(ScrapeAnomaly, target_id)
        current_configuration_version = (
            db.scalar(
                select(ScraperConfiguration.version).where(
                    ScraperConfiguration.id == anomaly.configuration_id
                )
            )
            if anomaly is not None
            else None
        )
        stale = anomaly is None or (
            target_dataset_checksum
            and target_dataset_checksum != anomaly.dataset_checksum[:8]
        ) or (
            target_configuration_version is not None
            and target_configuration_version
            != current_configuration_version
        )
        if stale:
            background_tasks.add_task(
                telegram.answer_callback,
                callback_query_id,
                "This decision is stale.",
            )
            return {"ok": True, "ignored": True}
    try:
        with db.begin_nested():
            db.add(WebhookReceipt(provider="telegram", event_key=update_id))
            db.flush()
    except IntegrityError:
        background_tasks.add_task(telegram.answer_callback, callback_query_id, "Already processed")
        return {"ok": True, "duplicate": True}
    if target_kind == "request":
        enqueue_job(
            db,
            "telegram_action",
            target_id,
            payload={
                "action": action,
                "callback_query_id": callback_query_id,
                "approver_user_id": user_id,
            },
        )
    elif target_kind == "anomaly":
        enqueue_job(
            db,
            "telegram_anomaly_action",
            payload={
                "action": action,
                "anomaly_id": str(target_id),
                "callback_query_id": callback_query_id,
                "approver_user_id": user_id,
            },
        )
    elif target_kind == "conflict":
        enqueue_job(
            db,
            "telegram_inventory_conflict_action",
            payload={
                "action": action,
                "conflict_id": str(target_id),
                "callback_query_id": callback_query_id,
                "approver_user_id": user_id,
            },
        )
    elif target_kind == "exclusion":
        enqueue_job(
            db,
            "telegram_exclusion_action",
            payload={
                "action": action,
                "exclusion_list_id": str(target_id),
                "callback_query_id": callback_query_id,
                "approver_user_id": user_id,
            },
        )
    else:
        enqueue_job(
            db,
            "telegram_recommendation_action",
            payload={
                "action": action,
                "recommendation_id": str(target_id),
                "callback_query_id": callback_query_id,
                "approver_user_id": user_id,
                "configuration_version": target_configuration_version,
                "evidence_checksum": target_evidence_checksum,
            },
        )
    db.commit()
    background_tasks.add_task(telegram.answer_callback, callback_query_id, "Queued")
    return {"ok": True, "queued": True}


@router.post("/api/integrations/resend/webhook")
async def resend_webhook(request: Request, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    body = await request.body()
    if not settings.resend_webhook_secret:
        raise HTTPException(status_code=503, detail="Resend webhook verification is not configured.")
    try:
        event = Webhook(settings.resend_webhook_secret).verify(body, dict(request.headers))
    except WebhookVerificationError:
        raise HTTPException(status_code=401, detail="Invalid Resend webhook signature.") from None
    event_key = request.headers.get("svix-id") or hashlib.sha256(body).hexdigest()
    try:
        with db.begin_nested():
            db.add(WebhookReceipt(provider="resend", event_key=event_key))
            db.flush()
    except IntegrityError:
        return {"ok": True, "duplicate": True}
    event_type = str(event.get("type") or "")
    message_id = str((event.get("data") or {}).get("email_id") or "")
    artifact = db.scalar(select(BatchArtifact).where(BatchArtifact.resend_message_id == message_id))
    if artifact:
        if event_type == "email.delivered":
            artifact.delivery_status = "delivered"
            artifact.last_error = ""
        elif event_type in {"email.bounced", "email.complained", "email.failed"}:
            artifact.delivery_status = "failed"
            artifact.last_error = event_type
            item = db.get(LeadRequest, artifact.request_id)
            if item:
                item.status = RequestStatus.failed.value
                item.status_message = f"Email provider reported {event_type.replace('email.', '')}."
                item.closed_at = utcnow()
                enqueue_milestone_email(db, item)
                enqueue_job(db, "update_notification", item.id)
    db.commit()
    return {"ok": True}
