from __future__ import annotations

import uuid
from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..activity import record_activity
from ..auth import Principal, require_admin
from ..config import Settings, get_settings
from ..database import get_db
from ..jobs import enqueue_job
from ..models import NightlyReview, ScrapeAnomaly
from ..schemas import ActionReason, NightlyDeliveryReconcile
from fastapi import APIRouter


router = APIRouter()


@router.get("/api/admin/nightly-reviews")
def list_nightly_reviews(
    telegram_delivery_state: str | None = None,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if telegram_delivery_state not in {
        None,
        "pending",
        "sending",
        "sent",
        "unknown",
    }:
        raise HTTPException(
            status_code=422,
            detail="Unknown Telegram delivery state.",
        )
    query = select(NightlyReview).order_by(
        NightlyReview.created_at.desc(),
        NightlyReview.id,
    )
    if telegram_delivery_state is not None:
        query = query.where(
            NightlyReview.telegram_delivery_state
            == telegram_delivery_state
        )
    return [
        {
            "id": str(review.id),
            "scraperRunId": review.scraper_run_id,
            "status": review.status,
            "summary": review.summary,
            "telegramDeliveryState": (
                review.telegram_delivery_state
            ),
            "telegramMessageId": review.telegram_message_id,
            "telegramDeliveryError": review.telegram_delivery_error,
            "telegramDeliveryStartedAt": (
                review.telegram_delivery_started_at
            ),
            "createdAt": review.created_at,
        }
        for review in db.scalars(query)
    ]


@router.post(
    "/api/admin/nightly-reviews/{review_id}/"
    "telegram-delivery/reconcile"
)
def reconcile_nightly_review_telegram_delivery(
    review_id: uuid.UUID,
    payload: NightlyDeliveryReconcile,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    review = db.scalar(
        select(NightlyReview)
        .where(NightlyReview.id == review_id)
        .with_for_update()
    )
    if review is None:
        raise HTTPException(
            status_code=404,
            detail="Nightly Review was not found.",
        )
    if review.telegram_delivery_state != "unknown":
        raise HTTPException(
            status_code=409,
            detail=(
                "Only an unknown Nightly Review delivery can be "
                "reconciled."
            ),
        )
    if payload.outcome == "delivered":
        review.telegram_message_id = payload.message_id or ""
        review.telegram_delivery_state = "sent"
        anomaly = db.scalar(
            select(ScrapeAnomaly).where(
                ScrapeAnomaly.scraper_run_id
                == review.scraper_run_id
            )
        )
        if anomaly is not None:
            anomaly.telegram_message_id = review.telegram_message_id
    else:
        review.telegram_message_id = ""
        review.telegram_delivery_state = "pending"
        enqueue_job(
            db,
            "notify_nightly_review",
            payload={"review_id": str(review.id)},
        )
    review.telegram_delivery_error = ""
    record_activity(
        db,
        action=(
            "nightly_review_telegram_delivered"
            if payload.outcome == "delivered"
            else "nightly_review_telegram_not_delivered"
        ),
        target_type="nightly_review",
        target_id=review.id,
        actor_id=principal.user_id,
        reason=payload.reason,
        details={
            "before": {"telegramDeliveryState": "unknown"},
            "after": {
                "telegramDeliveryState": review.telegram_delivery_state
            },
        },
    )
    db.commit()
    return {
        "id": str(review.id),
        "telegramDeliveryState": review.telegram_delivery_state,
        "telegramMessageId": review.telegram_message_id,
    }


@router.post("/api/admin/scrape-anomalies/{anomaly_id}/{action}")
def admin_scrape_anomaly_action(
    anomaly_id: uuid.UUID,
    action: str,
    payload: ActionReason,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Confirm or deny a held Scrape Anomaly from the browser.

    Deliberately a thin adapter over the same command the Telegram worker
    calls (jawnix/worker.py, the ``telegram_anomaly_action`` job). The dataset
    lock, the staged-file checksum guard, the supersede-on-newer-run rule, the
    idempotent duplicate return, ``record_activity()``, and the follow-up
    Telegram message edit all live inside ``decide_scrape_anomaly``. Deriving
    any of them again here would give one decision two behaviours, which is
    what #68 exists to prevent.
    """
    from jawnix_data.scraper import decide_scrape_anomaly

    if action not in {"confirm", "deny"}:
        raise HTTPException(status_code=404, detail="Unknown action.")
    try:
        result = decide_scrape_anomaly(
            db,
            settings,
            anomaly_id,
            action,
            str(principal.user_id),
            payload.reason,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except RuntimeError as exc:
        # The command raises RuntimeError for two different situations. A held
        # run or configuration that has gone missing is a broken reference no
        # retry resolves; a staged dataset that moved or changed underneath is
        # a genuine conflict. Reporting both as 409 would invite a retry that
        # can never succeed.
        detail = str(exc)
        raise HTTPException(
            status_code=404 if "was not found" in detail else 409,
            detail=detail,
        ) from None
    db.commit()
    return result
