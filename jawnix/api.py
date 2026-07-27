from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, Response
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from svix.webhooks import Webhook, WebhookVerificationError

from .auth import Principal, clear_session, issue_session, require_admin, require_principal, verify_supabase_token
from .config import Settings, get_settings
from .database import get_db
from .jobs import enqueue_job
from .models import (
    Agency,
    Customer,
    AuditEntry,
    BatchArtifact,
    CustomerProfile,
    CustomerTombstone,
    DistributionEvent,
    InventoryConflict,
    LeadDispositionState,
    LeadDispositionTransition,
    LeadReport,
    LeadReportResolution,
    LeadOutcome,
    Lead,
    LeadCorrectionEvent,
    ListingObservation,
    LeadRequest,
    NightlyReview,
    RequestStatus,
    ScraperConfiguration,
    ScrapeAnomaly,
    SourceRecommendation,
    SourceSegment,
    UserAccount,
    WebhookReceipt,
    utcnow,
)
from .schemas import (
    AgencyUpdate,
    ActionReason,
    LeadCorrectionApply,
    LeadReportCreate,
    LeadReportResolve,
    NightlyDeliveryReconcile,
    CustomerUpdate,
    CustomerCreate,
    CustomerDelete,
    DeleteConfirmation,
    FeedbackCreate,
    FeedbackLookup,
    OutcomeCreate,
    OutcomeOut,
    ProfileOut,
    ProfileUpdate,
    CustomerMappingUpdate,
    RequestCreate,
    RequestOut,
    SessionExchange,
    ScraperConfigurationCreate,
    UserAccountReplace,
)
from .scraper_proxy import (
    accept_scraper_handoff,
    clear_scraper_session,
    forward_scraper_request,
    request_is_scraper_origin,
    scraper_handoff_response,
    scraper_principal_from_request,
)
from .states import normalize_phone, normalize_states
from .telegram import (
    TelegramClient,
    parse_anomaly_callback_data,
    parse_callback_data,
    parse_conflict_callback_data,
    verify_telegram_secret,
)
from .transitions import TransitionError, transition_request


app = FastAPI(title="Jawnix VPS API", version="1.0.0")


@app.post("/admin/scraper/session")
async def scraper_operations_session(
    request: Request,
    settings: Settings = Depends(get_settings),
):
    return await accept_scraper_handoff(request, settings)


@app.post("/admin/scraper/logout")
def scraper_operations_logout(
    request: Request,
    settings: Settings = Depends(get_settings),
):
    return clear_scraper_session(request, settings)


@app.api_route(
    "/admin/scraper",
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"],
)
@app.api_route(
    "/admin/scraper/{path:path}",
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"],
)
async def scraper_operations(
    request: Request,
    path: str = "",
    settings: Settings = Depends(get_settings),
):
    if request_is_scraper_origin(request, settings):
        principal = scraper_principal_from_request(request, settings)
    else:
        principal = require_admin(require_principal(request, settings))
        return scraper_handoff_response(principal, settings)
    return await forward_scraper_request(
        request,
        path,
        principal,
        settings,
    )


@app.get("/api/healthz")
def healthz(settings: Settings = Depends(get_settings)):
    return {"ok": True, "billingEnabled": settings.billing_enabled}


@app.get("/api/readyz")
def readyz(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"ok": True}


@app.get("/api/admin/nightly-reviews")
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


@app.post(
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
    _audit(
        db,
        principal,
        (
            "nightly_review_telegram_delivered"
            if payload.outcome == "delivered"
            else "nightly_review_telegram_not_delivered"
        ),
        "nightly_review",
        review.id,
        payload.reason,
        {"messageId": review.telegram_message_id},
    )
    db.commit()
    return {
        "id": str(review.id),
        "telegramDeliveryState": review.telegram_delivery_state,
        "telegramMessageId": review.telegram_message_id,
    }


@app.post("/api/auth/session")
async def create_session(
    payload: SessionExchange,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    user = await verify_supabase_token(payload.access_token, settings)
    role = str((user.get("app_metadata") or {}).get("jawnix_role") or "customer")
    if payload.requested_next == "/admin.html" and role != "admin":
        raise HTTPException(status_code=403, detail="Sign in with noah@jawnix.com to access administration.")
    principal = issue_session(response, user, settings)
    profile = db.get(CustomerProfile, principal.user_id)
    account = db.get(UserAccount, principal.user_id)
    if account is not None and not account.active:
        raise HTTPException(
            status_code=403,
            detail="This User Account has been replaced or deactivated.",
        )
    if profile is None and principal.role != "admin":
        metadata = user.get("user_metadata") or {}
        profile = CustomerProfile(
            user_id=principal.user_id,
            email=principal.email,
            first_name=str(metadata.get("first_name") or ""),
            last_name=str(metadata.get("last_name") or ""),
            licensed_states=[],
        )
        db.add(profile)
    elif profile is not None:
        profile.email = principal.email
        if profile.customer_id is not None and principal.role != "admin":
            current_account = db.scalar(
                select(UserAccount).where(
                    UserAccount.customer_id == profile.customer_id,
                    UserAccount.active.is_(True),
                )
            )
            if account is None:
                if current_account is not None:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "This Customer already has an active User "
                            "Account. Ask an administrator to replace it."
                        ),
                    )
                db.add(
                    UserAccount(
                        auth_user_id=principal.user_id,
                        customer_id=profile.customer_id,
                        email=principal.email,
                        active=True,
                    )
                )
            elif account.customer_id != profile.customer_id:
                raise HTTPException(
                    status_code=409,
                    detail="User Account and Customer mapping do not match.",
                )
    db.commit()
    return {"ok": True, "role": principal.role, "next": "/admin.html" if principal.role == "admin" else "/portal.html"}


@app.post("/api/auth/logout")
def logout(
    response: Response,
    _: Principal = Depends(require_principal),
    settings: Settings = Depends(get_settings),
):
    clear_session(response, settings)
    return {"ok": True}


@app.get("/api/me/profile", response_model=ProfileOut)
def get_profile(principal: Principal = Depends(require_principal), db: Session = Depends(get_db)):
    account = db.get(UserAccount, principal.user_id)
    if account is not None and not account.active:
        raise HTTPException(
            status_code=403,
            detail="This User Account has been replaced.",
        )
    profile = db.get(CustomerProfile, principal.user_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile was not found.")
    return profile


@app.patch("/api/me/profile", response_model=ProfileOut)
def update_profile(
    payload: ProfileUpdate,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
):
    profile = db.scalar(
        select(CustomerProfile)
        .where(CustomerProfile.user_id == principal.user_id)
        .with_for_update()
    )
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile was not found.")
    previous_states = normalize_states(profile.licensed_states)
    next_states = normalize_states(payload.licensed_states)
    removed_states = sorted(set(previous_states) - set(next_states))
    added_states = sorted(set(next_states) - set(previous_states))

    if removed_states:
        unallocated_statuses = {
            RequestStatus.pending.value,
            RequestStatus.approved.value,
            RequestStatus.waiting_inventory.value,
        }
        requests = list(
            db.scalars(
                select(LeadRequest)
                .where(
                    LeadRequest.user_id == principal.user_id,
                    LeadRequest.status.in_(unallocated_statuses),
                )
                .order_by(LeadRequest.created_at, LeadRequest.id)
                .with_for_update()
            )
        )
        for item in requests:
            narrowed_states = [
                state for state in item.states_snapshot if state in next_states
            ]
            if narrowed_states == item.states_snapshot:
                continue
            item.states_snapshot = narrowed_states
            if narrowed_states:
                action = "narrowed"
                item.status_message = (
                    "Licensed States changed. Removed "
                    f"{', '.join(removed_states)}; request now covers "
                    f"{', '.join(narrowed_states)}. Existing approval remains valid."
                )
            else:
                action = "canceled"
                item.status = RequestStatus.canceled.value
                item.status_message = (
                    "Canceled because no requested states remain in the "
                    "Customer's Licensed States."
                )
            enqueue_job(
                db,
                "licensed_states_changed",
                item.id,
                {
                    "added": added_states,
                    "removed": removed_states,
                    "requestAction": action,
                    "states": narrowed_states,
                },
            )

    profile.first_name = payload.first_name.strip()
    profile.last_name = payload.last_name.strip()
    profile.phone = payload.phone.strip()
    profile.licensed_states = next_states
    if profile.customer is not None:
        profile.customer.licensed_states = next_states
    profile.updated_at = utcnow()
    db.commit()
    db.refresh(profile)
    return profile


@app.get("/api/me/requests", response_model=list[RequestOut])
def list_my_requests(principal: Principal = Depends(require_principal), db: Session = Depends(get_db)):
    return list(
        db.scalars(
            select(LeadRequest).where(LeadRequest.user_id == principal.user_id).order_by(LeadRequest.created_at.desc())
        )
    )


_OUTCOME_METRICS = {
    "good": "quality",
    "poor": "quality",
    "positive_response": "positive_response",
    "appointment_booked": "appointment_booked",
    "appointment_canceled": "appointment_canceled",
    "appointment_no_show": "appointment_no_show",
}


def _feedback_not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail="No delivered Lead found.",
    )


@app.post("/api/me/feedback/lookup")
def lookup_feedback(
    payload: FeedbackLookup,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
):
    profile = db.get(CustomerProfile, principal.user_id)
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
    profile = db.get(CustomerProfile, principal.user_id)
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


def _disposition_transition_response(
    item: LeadDispositionTransition,
) -> dict:
    return {
        "id": str(item.id),
        "distributionEventId": item.distribution_event_id,
        "disposition": item.disposition,
        "note": item.note,
        "actorUserId": str(item.actor_user_id),
        "previousTransitionId": (
            str(item.previous_transition_id)
            if item.previous_transition_id is not None
            else None
        ),
        "createdAt": item.created_at,
    }


def _customer_feedback_distribution(
    db: Session,
    principal: Principal,
    event_id: int,
) -> tuple[CustomerProfile, DistributionEvent]:
    try:
        return _customer_distribution(
            db,
            principal,
            event_id,
        )
    except HTTPException as error:
        if error.status_code == 404:
            raise _feedback_not_found() from None
        raise


def _quality_rating_response(item: LeadOutcome) -> dict:
    return {
        "id": str(item.id),
        "kind": item.kind,
        "note": item.note,
        "supersedesOutcomeId": (
            str(item.supersedes_outcome_id)
            if item.supersedes_outcome_id is not None
            else None
        ),
        "createdAt": item.created_at,
    }


@app.get("/api/me/distributions/{event_id}/dispositions")
def list_dispositions(
    event_id: int,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
):
    _, event = _customer_feedback_distribution(
        db,
        principal,
        event_id,
    )
    return [
        _disposition_transition_response(item)
        for item in db.scalars(
            select(LeadDispositionTransition)
            .where(
                LeadDispositionTransition.distribution_event_id
                == event.id
            )
            .order_by(
                LeadDispositionTransition.created_at,
                LeadDispositionTransition.id,
            )
        )
    ]


@app.post("/api/me/feedback", status_code=201)
def create_feedback(
    payload: FeedbackCreate,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
):
    profile, event = _customer_feedback_distribution(
        db,
        principal,
        payload.distribution_event_id,
    )
    event = db.scalar(
        select(DistributionEvent)
        .where(DistributionEvent.id == event.id)
        .with_for_update()
    )
    previous = db.scalar(
        select(LeadDispositionTransition)
        .where(
            LeadDispositionTransition.distribution_event_id
            == event.id
        )
        .order_by(
            LeadDispositionTransition.created_at.desc(),
            LeadDispositionTransition.id.desc(),
        )
        .limit(1)
    )
    quality_rating = None
    if payload.quality_rating is not None:
        rating_history = list(
            db.scalars(
                select(LeadOutcome)
                .where(
                    LeadOutcome.distribution_event_id
                    == event.id,
                    LeadOutcome.metric == "quality",
                )
                .order_by(
                    LeadOutcome.created_at,
                    LeadOutcome.id,
                )
            )
        )
        superseded_rating_ids = {
            item.supersedes_outcome_id
            for item in rating_history
            if item.supersedes_outcome_id is not None
        }
        active_rating = next(
            (
                item
                for item in reversed(rating_history)
                if item.id not in superseded_rating_ids
            ),
            None,
        )
        quality_rating = LeadOutcome(
            distribution_event_id=event.id,
            customer_id=profile.customer_id,
            kind=payload.quality_rating,
            metric="quality",
            note=payload.quality_note.strip(),
            supersedes_outcome_id=(
                active_rating.id
                if active_rating is not None
                else None
            ),
        )
    changed_at = utcnow()
    transition = LeadDispositionTransition(
        distribution_event_id=event.id,
        customer_id=profile.customer_id,
        actor_user_id=principal.user_id,
        disposition=payload.disposition,
        note=payload.note.strip(),
        previous_transition_id=(
            previous.id if previous is not None else None
        ),
        created_at=changed_at,
    )
    db.add(transition)
    if quality_rating is not None:
        db.add(quality_rating)
    db.flush()
    disposition_state = db.get(
        LeadDispositionState,
        event.id,
    )
    if disposition_state is None:
        disposition_state = LeadDispositionState(
            distribution_event_id=event.id,
            current_transition_id=transition.id,
            current_disposition=payload.disposition,
            updated_at=changed_at,
        )
        db.add(disposition_state)
    else:
        disposition_state.current_transition_id = transition.id
        disposition_state.current_disposition = payload.disposition
        disposition_state.updated_at = changed_at
    db.commit()
    db.refresh(transition)
    if quality_rating is not None:
        db.refresh(quality_rating)
    return {
        "distributionEventId": event.id,
        "currentDisposition": (
            disposition_state.current_disposition
        ),
        "transition": _disposition_transition_response(
            transition
        ),
        "qualityRating": (
            _quality_rating_response(quality_rating)
            if quality_rating is not None
            else None
        ),
    }


@app.get(
    "/api/me/distributions/{event_id}/outcomes",
    response_model=list[OutcomeOut],
)
def list_outcomes(
    event_id: int,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
):
    _, event = _customer_distribution(db, principal, event_id)
    return list(
        db.scalars(
            select(LeadOutcome)
            .where(LeadOutcome.distribution_event_id == event.id)
            .order_by(LeadOutcome.created_at, LeadOutcome.id)
        )
    )


@app.post(
    "/api/me/distributions/{event_id}/outcomes",
    response_model=OutcomeOut,
    status_code=201,
    deprecated=True,
)
def create_outcome(
    event_id: int,
    payload: OutcomeCreate,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
):
    """Retain the legacy Outcome write contract during feedback migration."""
    profile, event = _customer_distribution(db, principal, event_id)
    metric = _OUTCOME_METRICS[payload.kind]
    history = list(
        db.scalars(
            select(LeadOutcome)
            .where(LeadOutcome.distribution_event_id == event.id)
            .order_by(LeadOutcome.created_at, LeadOutcome.id)
            .with_for_update()
        )
    )
    superseded_ids = {
        item.supersedes_outcome_id
        for item in history
        if item.supersedes_outcome_id is not None
    }
    active_by_metric = {
        item.metric: item
        for item in history
        if item.id not in superseded_ids
    }
    if payload.kind in {"appointment_canceled", "appointment_no_show"}:
        if "appointment_booked" not in active_by_metric:
            raise HTTPException(
                status_code=409,
                detail="Book an appointment before reporting its later status.",
            )
    if payload.supersedes_outcome_id is not None:
        previous = next(
            (
                item
                for item in history
                if item.id == payload.supersedes_outcome_id
            ),
            None,
        )
        if (
            previous is None
            or previous.metric != metric
            or previous.id in superseded_ids
        ):
            raise HTTPException(
                status_code=409,
                detail="The selected outcome cannot be superseded.",
            )
    elif metric in active_by_metric:
        raise HTTPException(
            status_code=409,
            detail="This milestone is already recorded; submit a correction instead.",
        )
    outcome = LeadOutcome(
        distribution_event_id=event.id,
        customer_id=profile.customer_id,
        kind=payload.kind,
        metric=metric,
        appointment_at=payload.appointment_at,
        note=payload.note.strip(),
        supersedes_outcome_id=payload.supersedes_outcome_id,
    )
    db.add(outcome)
    db.commit()
    db.refresh(outcome)
    return outcome


@app.post(
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


@app.get("/api/admin/source-performance")
def source_performance(
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    from .performance import source_performance_snapshot

    return source_performance_snapshot(db)


@app.get("/api/admin/source-recommendations")
def list_source_recommendations(
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return [
        {
            "id": str(item.id),
            "niche": item.niche,
            "segment": item.segment_key,
            "action": item.action,
            "status": item.status,
            "evidence": item.evidence,
            "resultingConfigurationId": (
                str(item.resulting_configuration_id)
                if item.resulting_configuration_id is not None
                else None
            ),
        }
        for item in db.scalars(
            select(SourceRecommendation).order_by(
                SourceRecommendation.created_at.desc(),
                SourceRecommendation.id,
            )
        )
    ]


@app.post(
    "/api/admin/source-recommendations/{recommendation_id}/{action}"
)
def decide_source_recommendation(
    recommendation_id: uuid.UUID,
    action: str,
    payload: ActionReason,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if action not in {"approve", "deny"}:
        raise HTTPException(status_code=404, detail="Unknown action.")
    recommendation = db.scalar(
        select(SourceRecommendation)
        .where(SourceRecommendation.id == recommendation_id)
        .with_for_update()
    )
    if recommendation is None:
        raise HTTPException(
            status_code=404,
            detail="Source Recommendation was not found.",
        )
    if recommendation.status != "pending":
        return {
            "id": str(recommendation.id),
            "status": recommendation.status,
            "duplicate": True,
        }
    recommendation.status = "approved" if action == "approve" else "denied"
    recommendation.decision_by = str(principal.user_id)
    recommendation.decision_reason = payload.reason.strip()
    recommendation.decided_at = utcnow()
    if action == "approve":
        latest = db.scalar(
            select(ScraperConfiguration)
            .order_by(ScraperConfiguration.version.desc())
            .limit(1)
            .with_for_update()
        )
        if latest is None:
            raise HTTPException(
                status_code=409,
                detail="Create a Scraper Configuration first.",
            )
        for scheduled in db.scalars(
            select(ScraperConfiguration)
            .where(ScraperConfiguration.status == "scheduled")
            .with_for_update()
        ):
            scheduled.status = "schedule_replaced"
        segments = []
        for segment in latest.segments:
            parameters = dict(segment.parameters)
            if segment.key == recommendation.segment_key:
                parameters["recommendation_action"] = recommendation.action
                parameters["enabled"] = recommendation.action != "pause"
                parameters["relative_weight"] = {
                    "expand": 1.25,
                    "reduce": 0.75,
                    "pause": 0.0,
                }[recommendation.action]
            segments.append(
                SourceSegment(
                    key=segment.key,
                    niche=segment.niche,
                    query=segment.query,
                    geography=segment.geography,
                    parameters=parameters,
                )
            )
        canonical = json.dumps(
            {
                "segments": [
                    {
                        "key": item.key,
                        "niche": item.niche,
                        "query": item.query,
                        "geography": item.geography,
                        "parameters": item.parameters,
                    }
                    for item in segments
                ],
                "anomaly_thresholds": latest.anomaly_thresholds,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        next_configuration = ScraperConfiguration(
            version=latest.version + 1,
            checksum=hashlib.sha256(
                canonical.encode("utf-8")
            ).hexdigest(),
            status="scheduled",
            anomaly_thresholds=latest.anomaly_thresholds,
            created_by=principal.user_id,
            reason=payload.reason.strip(),
            scheduled_at=utcnow(),
            based_on_configuration_id=latest.id,
            segments=segments,
        )
        db.add(next_configuration)
        db.flush()
        recommendation.resulting_configuration_id = next_configuration.id
    _audit(
        db,
        principal,
        f"source_recommendation_{recommendation.status}",
        "source_recommendation",
        recommendation.id,
        payload.reason,
        {
            "proposedAction": recommendation.action,
            "segment": recommendation.segment_key,
            "resultingConfigurationId": (
                str(recommendation.resulting_configuration_id)
                if recommendation.resulting_configuration_id is not None
                else None
            ),
        },
    )
    db.commit()
    return {
        "id": str(recommendation.id),
        "status": recommendation.status,
        "resultingConfigurationId": (
            str(recommendation.resulting_configuration_id)
            if recommendation.resulting_configuration_id is not None
            else None
        ),
    }


@app.post("/api/me/requests", response_model=RequestOut, status_code=201)
def create_request(
    payload: RequestCreate,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
):
    profile = db.get(CustomerProfile, principal.user_id)
    if (
        profile is None
        or profile.customer is None
        or not profile.customer.active
        or profile.customer.deleted_at is not None
        or (
            profile.customer.agency is not None
            and (
                not profile.customer.agency.active
                or profile.customer.agency.deleted_at is not None
            )
        )
        or profile.mapping_confirmed_at is None
    ):
        raise HTTPException(
            status_code=409,
            detail="Your distribution mapping must be active and confirmed before requesting a batch.",
        )
    saved_states = normalize_states(profile.licensed_states)
    if not saved_states:
        raise HTTPException(status_code=422, detail="Save at least one licensed state first.")
    states = saved_states if payload.state_mode == "all_saved" else payload.states
    if not set(states).issubset(saved_states):
        raise HTTPException(status_code=422, detail="Request states must be selected from your saved profile states.")
    request = LeadRequest(
        user_id=principal.user_id,
        agent_id=profile.customer_id,
        lead_count=payload.lead_count,
        state_mode=payload.state_mode,
        states_snapshot=states,
        delivery_email=profile.email,
        status=RequestStatus.pending.value,
        status_message="Awaiting Telegram approval.",
    )
    db.add(request)
    db.flush()
    enqueue_job(db, "notify_request", request.id)
    db.commit()
    db.refresh(request)
    return request


@app.delete("/api/me/requests/{request_id}")
def cancel_request(
    request_id: uuid.UUID,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
):
    item = db.scalar(
        select(LeadRequest)
        .where(LeadRequest.id == request_id, LeadRequest.user_id == principal.user_id)
        .with_for_update()
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Request was not found.")
    if item.status not in {
        RequestStatus.pending.value,
        RequestStatus.approved.value,
        RequestStatus.waiting_inventory.value,
    }:
        raise HTTPException(
            status_code=409,
            detail="Only uncommitted requests can be canceled.",
        )
    item.status = RequestStatus.canceled.value
    item.status_message = "Canceled by customer."
    enqueue_job(db, "update_notification", item.id)
    db.commit()
    return {"ok": True}


def _request_dict(item: LeadRequest) -> dict:
    return {
        "id": str(item.id),
        "userId": str(item.user_id),
        "customer": " ".join(part for part in (item.profile.first_name, item.profile.last_name) if part).strip() or item.profile.email,
        "email": item.delivery_email,
        "customerIdentity": item.customer.name,
        "leadCount": item.lead_count,
        "states": item.states_snapshot,
        "status": item.status,
        "availableCount": item.available_count,
        "statusMessage": item.status_message,
        "createdAt": item.created_at,
        "deliveredAt": item.delivered_at,
        "hasArtifact": item.artifact is not None,
    }


@app.get("/api/admin/requests")
def admin_requests(_: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    return [_request_dict(item) for item in db.scalars(select(LeadRequest).order_by(LeadRequest.created_at.desc()))]


@app.get("/api/admin/recipients", include_in_schema=False)
def admin_recipients(_: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    profiles = list(db.scalars(select(CustomerProfile).order_by(CustomerProfile.email)))
    agencies = list(db.scalars(select(Agency).where(Agency.deleted_at.is_(None)).order_by(Agency.name)))
    agents = list(
        db.scalars(
            select(Customer)
            .where(Customer.deleted_at.is_(None))
            .order_by(Customer.name)
        )
    )
    return {
        "recipients": [
            {
                "userId": str(profile.user_id),
                "email": profile.email,
                "name": " ".join(part for part in (profile.first_name, profile.last_name) if part).strip(),
                "states": profile.licensed_states,
                "agentId": profile.customer_id,
                "agent": (
                    profile.customer.name if profile.customer else ""
                ),
                "confirmed": profile.mapping_confirmed_at is not None,
            }
            for profile in profiles
        ],
        "agencies": [
            {"id": agency.id, "slug": agency.slug, "name": agency.name, "active": agency.active}
            for agency in agencies
        ],
        "agents": [
            {
                "id": agent.id,
                "slug": agent.slug,
                "name": agent.name,
                "active": agent.active,
                "agencyId": agent.agency_id,
                "agency": agent.agency.name if agent.agency else "",
            }
            for agent in agents
        ],
    }


@app.get("/api/admin/customers")
def admin_customers(
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    customers = list(
        db.scalars(
            select(Customer)
            .where(Customer.deleted_at.is_(None))
            .order_by(Customer.name)
        )
    )
    profiles = list(
        db.scalars(
            select(CustomerProfile).order_by(CustomerProfile.email)
        )
    )
    agencies = list(
        db.scalars(
            select(Agency)
            .where(Agency.deleted_at.is_(None))
            .order_by(Agency.name)
        )
    )
    return {
        "userAccounts": [
            {
                "userId": str(profile.user_id),
                "email": profile.email,
                "name": " ".join(
                    part
                    for part in (
                        profile.first_name,
                        profile.last_name,
                    )
                    if part
                ).strip(),
                "states": profile.licensed_states,
                "customerId": profile.customer_id,
                "customer": (
                    profile.customer.name if profile.customer else ""
                ),
                "confirmed": profile.mapping_confirmed_at is not None,
            }
            for profile in profiles
        ],
        "agencies": [
            {
                "id": agency.id,
                "slug": agency.slug,
                "name": agency.name,
                "active": agency.active,
            }
            for agency in agencies
        ],
        "customers": [
            {
                "id": customer.id,
                "slug": customer.slug,
                "name": customer.name,
                "active": customer.active,
                "agencyId": customer.agency_id,
                "agency": customer.agency.name if customer.agency else "",
            }
            for customer in customers
        ]
    }


def _configuration_dict(item: ScraperConfiguration) -> dict:
    return {
        "id": str(item.id),
        "version": item.version,
        "checksum": item.checksum,
        "status": item.status,
        "createdBy": str(item.created_by),
        "reason": item.reason,
        "anomalyThresholds": item.anomaly_thresholds,
        "createdAt": item.created_at,
        "scheduledAt": item.scheduled_at,
        "activatedAt": item.activated_at,
        "basedOnConfigurationId": (
            str(item.based_on_configuration_id)
            if item.based_on_configuration_id
            else None
        ),
        "segments": [
            {
                "key": segment.key,
                "niche": segment.niche,
                "query": segment.query,
                "geography": segment.geography,
                "parameters": segment.parameters,
            }
            for segment in item.segments
        ],
    }


@app.get("/api/admin/scraper-configurations")
def list_scraper_configurations(
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return [
        _configuration_dict(item)
        for item in db.scalars(
            select(ScraperConfiguration).order_by(
                ScraperConfiguration.version.desc()
            )
        )
    ]


@app.get("/api/admin/scraper-configurations/{configuration_id}")
def get_scraper_configuration(
    configuration_id: uuid.UUID,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    item = db.get(ScraperConfiguration, configuration_id)
    if item is None:
        raise HTTPException(
            status_code=404,
            detail="Scraper Configuration was not found.",
        )
    return _configuration_dict(item)


@app.post(
    "/api/admin/scraper-configurations",
    status_code=201,
)
def create_scraper_configuration(
    payload: ScraperConfigurationCreate,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    latest = db.scalar(
        select(ScraperConfiguration)
        .order_by(ScraperConfiguration.version.desc())
        .limit(1)
        .with_for_update()
    )
    version = (latest.version if latest else 0) + 1
    segments = [
        {
            "key": segment.key.strip().lower(),
            "niche": segment.niche.strip(),
            "query": segment.query.strip(),
            "geography": segment.geography.strip(),
            "parameters": segment.parameters,
        }
        for segment in payload.segments
    ]
    segments.sort(key=lambda segment: segment["key"])
    thresholds = payload.anomaly_thresholds.model_dump()
    canonical = json.dumps(
        {
            "segments": segments,
            "anomaly_thresholds": thresholds,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    item = ScraperConfiguration(
        version=version,
        checksum=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        status="draft",
        anomaly_thresholds=thresholds,
        created_by=principal.user_id,
        reason=payload.reason.strip(),
        segments=[
            SourceSegment(**segment)
            for segment in segments
        ],
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return _configuration_dict(item)


def _audit(
    db: Session,
    principal: Principal,
    action: str,
    target_type: str,
    target_id: object,
    reason: str,
    details: dict | None = None,
) -> None:
    db.add(
        AuditEntry(
            action=action,
            target_type=target_type,
            target_id=str(target_id),
            actor_user_id=str(principal.user_id),
            reason=reason.strip(),
            details=details or {},
        )
    )


@app.post(
    "/api/admin/scraper-configurations/{configuration_id}/schedule"
)
def schedule_scraper_configuration(
    configuration_id: uuid.UUID,
    payload: ActionReason,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    item = db.scalar(
        select(ScraperConfiguration)
        .where(ScraperConfiguration.id == configuration_id)
        .with_for_update()
    )
    if item is None:
        raise HTTPException(
            status_code=404,
            detail="Scraper Configuration was not found.",
        )
    for scheduled in db.scalars(
        select(ScraperConfiguration)
        .where(
            ScraperConfiguration.status == "scheduled",
            ScraperConfiguration.id != item.id,
        )
        .with_for_update()
    ):
        scheduled.status = "schedule_replaced"
    item.status = "scheduled"
    item.scheduled_at = utcnow()
    _audit(
        db,
        principal,
        "scraper_configuration_scheduled",
        "scraper_configuration",
        item.id,
        payload.reason,
        {"version": item.version},
    )
    db.commit()
    db.refresh(item)
    return _configuration_dict(item)


@app.post(
    "/api/admin/scraper-configurations/{configuration_id}/manual-run",
    status_code=202,
)
def queue_manual_scrape_run(
    configuration_id: uuid.UUID,
    payload: ActionReason,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    item = db.get(ScraperConfiguration, configuration_id)
    if item is None:
        raise HTTPException(
            status_code=404,
            detail="Scraper Configuration was not found.",
        )
    job_payload = {
        "configuration_id": str(item.id),
        "reason": payload.reason.strip(),
        "actor_user_id": str(principal.user_id),
        "manual": True,
    }
    enqueue_job(db, "run_scraper", payload=job_payload)
    _audit(
        db,
        principal,
        "scraper_manual_run_queued",
        "scraper_configuration",
        item.id,
        payload.reason,
        {"version": item.version},
    )
    db.commit()
    return {
        "queued": True,
        "configurationId": str(item.id),
    }


@app.post(
    "/api/admin/scraper-configurations/{configuration_id}/rollback",
    status_code=201,
)
def rollback_scraper_configuration(
    configuration_id: uuid.UUID,
    payload: ActionReason,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    source = db.get(ScraperConfiguration, configuration_id)
    if source is None:
        raise HTTPException(
            status_code=404,
            detail="Scraper Configuration was not found.",
        )
    latest = db.scalar(
        select(ScraperConfiguration)
        .order_by(ScraperConfiguration.version.desc())
        .limit(1)
        .with_for_update()
    )
    for scheduled in db.scalars(
        select(ScraperConfiguration)
        .where(ScraperConfiguration.status == "scheduled")
        .with_for_update()
    ):
        scheduled.status = "schedule_replaced"
    item = ScraperConfiguration(
        version=(latest.version if latest else 0) + 1,
        checksum=source.checksum,
        status="scheduled",
        anomaly_thresholds=source.anomaly_thresholds,
        created_by=principal.user_id,
        reason=payload.reason.strip(),
        scheduled_at=utcnow(),
        based_on_configuration_id=source.id,
        segments=[
            SourceSegment(
                key=segment.key,
                niche=segment.niche,
                query=segment.query,
                geography=segment.geography,
                parameters=segment.parameters,
            )
            for segment in source.segments
        ],
    )
    db.add(item)
    db.flush()
    _audit(
        db,
        principal,
        "scraper_configuration_rollback_scheduled",
        "scraper_configuration",
        item.id,
        payload.reason,
        {
            "version": item.version,
            "basedOnConfigurationId": str(source.id),
        },
    )
    db.commit()
    db.refresh(item)
    return _configuration_dict(item)


@app.put("/api/admin/leads/{lead_id}/suppression")
def suppress_lead(
    lead_id: int,
    payload: ActionReason,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    lead = db.scalar(
        select(Lead).where(Lead.id == lead_id).with_for_update()
    )
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead was not found.")
    if not lead.suppressed:
        lead.suppressed = True
        lead.suppression_reason = payload.reason.strip()
        _audit(
            db,
            principal,
            "lead_suppressed",
            "lead",
            lead.id,
            payload.reason,
            {"phone": lead.phone},
        )
    db.commit()
    return {
        "leadId": lead.id,
        "suppressed": lead.suppressed,
        "reason": lead.suppression_reason,
    }


@app.delete("/api/admin/leads/{lead_id}/suppression")
def unsuppress_lead(
    lead_id: int,
    payload: ActionReason,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    lead = db.scalar(
        select(Lead).where(Lead.id == lead_id).with_for_update()
    )
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead was not found.")
    if lead.suppressed:
        previous_reason = lead.suppression_reason
        lead.suppressed = False
        lead.suppression_reason = ""
        _audit(
            db,
            principal,
            "lead_unsuppressed",
            "lead",
            lead.id,
            payload.reason,
            {
                "phone": lead.phone,
                "previousSuppressionReason": previous_reason,
            },
        )
    db.commit()
    return {
        "leadId": lead.id,
        "suppressed": lead.suppressed,
        "reason": lead.suppression_reason,
    }


@app.put("/api/admin/leads/{lead_id}/correction")
def apply_lead_correction(
    lead_id: int,
    payload: LeadCorrectionApply,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    lead = db.scalar(
        select(Lead).where(Lead.id == lead_id).with_for_update()
    )
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead was not found.")
    previous_id = lead.active_correction_id
    correction = LeadCorrectionEvent(
        lead_id=lead.id,
        action="applied",
        title=payload.title.strip() if payload.title is not None else lead.title,
        state=payload.state or lead.state,
        actor_id=str(principal.user_id),
        reason=payload.reason.strip(),
        supersedes_correction_id=previous_id,
    )
    db.add(correction)
    db.flush()
    lead.active_correction_id = correction.id
    lead.title = correction.title
    lead.state = correction.state
    _audit(
        db,
        principal,
        "lead_correction_applied",
        "lead",
        lead.id,
        payload.reason,
        {"correctionId": str(correction.id)},
    )
    db.commit()
    return {
        "leadId": lead.id,
        "correctionId": str(correction.id),
        "title": lead.title,
        "state": lead.state,
    }


@app.delete("/api/admin/leads/{lead_id}/correction")
def remove_lead_correction(
    lead_id: int,
    payload: ActionReason,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    lead = db.scalar(
        select(Lead).where(Lead.id == lead_id).with_for_update()
    )
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead was not found.")
    if lead.active_correction_id is None:
        raise HTTPException(
            status_code=409,
            detail="Lead has no active correction.",
        )
    active = db.get(LeadCorrectionEvent, lead.active_correction_id)
    removal = LeadCorrectionEvent(
        lead_id=lead.id,
        action="removed",
        title=active.title,
        state=active.state,
        actor_id=str(principal.user_id),
        reason=payload.reason.strip(),
        supersedes_correction_id=active.id,
    )
    db.add(removal)
    lead.active_correction_id = None
    observation = (
        db.get(ListingObservation, lead.current_listing_observation_id)
        if lead.current_listing_observation_id is not None
        else None
    )
    if observation is not None:
        lead.title = observation.title
        lead.state = observation.state
    else:
        lead.title = lead.legacy_title
        lead.state = lead.legacy_state
    _audit(
        db,
        principal,
        "lead_correction_removed",
        "lead",
        lead.id,
        payload.reason,
        {"correctionId": str(active.id)},
    )
    db.commit()
    return {
        "leadId": lead.id,
        "correctionId": None,
        "title": lead.title,
        "state": lead.state,
    }


@app.post("/api/admin/lead-reports/{report_id}/resolve")
def resolve_lead_report(
    report_id: uuid.UUID,
    payload: LeadReportResolve,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    report = db.scalar(
        select(LeadReport)
        .where(LeadReport.id == report_id)
        .with_for_update()
    )
    if report is None:
        raise HTTPException(status_code=404, detail="Lead Report was not found.")
    if report.status != "open":
        raise HTTPException(
            status_code=409,
            detail="Lead Report is already resolved.",
        )
    event = db.get(DistributionEvent, report.distribution_event_id)
    lead = db.scalar(
        select(Lead).where(Lead.id == event.lead_id).with_for_update()
    )
    resolution = LeadReportResolution(
        report_id=report.id,
        action=payload.action,
        note=payload.note.strip(),
        actor_id=str(principal.user_id),
    )
    db.add(resolution)
    if payload.action == "corrected":
        correction = LeadCorrectionEvent(
            lead_id=lead.id,
            action="applied",
            title=(
                payload.title.strip()
                if payload.title is not None
                else lead.title
            ),
            state=payload.state or lead.state,
            actor_id=str(principal.user_id),
            reason=payload.note.strip(),
            supersedes_correction_id=lead.active_correction_id,
        )
        db.add(correction)
        db.flush()
        lead.active_correction_id = correction.id
        lead.title = correction.title
        lead.state = correction.state
    elif payload.action == "suppressed":
        lead.suppressed = True
        lead.suppression_reason = payload.note.strip()
    report.status = payload.action
    _audit(
        db,
        principal,
        f"lead_report_{payload.action}",
        "lead_report",
        report.id,
        payload.note,
        {
            "distributionEventId": event.id,
            "leadId": lead.id,
            "reportReason": report.reason,
        },
    )
    db.commit()
    return {
        "reportId": str(report.id),
        "status": report.status,
        "resolutionId": str(resolution.id),
    }


@app.put("/api/admin/customers/{customer_id}/user-account")
def replace_user_account(
    customer_id: int,
    payload: UserAccountReplace,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    customer = db.scalar(
        select(Customer)
        .where(
            Customer.id == customer_id,
            Customer.deleted_at.is_(None),
        )
        .with_for_update()
    )
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer was not found.")
    if not customer.active:
        raise HTTPException(
            status_code=409,
            detail="A User Account cannot be assigned to a deactivated Customer.",
        )
    now = utcnow()
    current_accounts = list(
        db.scalars(
            select(UserAccount)
            .where(
                UserAccount.customer_id == customer.id,
                UserAccount.active.is_(True),
            )
            .with_for_update()
        )
    )
    for account in current_accounts:
        account.active = False
        account.replaced_at = now

    account = db.get(UserAccount, payload.auth_user_id)
    if account is None:
        account = UserAccount(
            auth_user_id=payload.auth_user_id,
            customer_id=customer.id,
            email=str(payload.email).lower(),
            active=True,
        )
        db.add(account)
    else:
        account.customer_id = customer.id
        account.email = str(payload.email).lower()
        account.active = True
        account.replaced_at = None

    licensed_states = normalize_states(customer.licensed_states)
    profile = db.get(CustomerProfile, payload.auth_user_id)
    if profile is None:
        profile = CustomerProfile(
            user_id=payload.auth_user_id,
            email=account.email,
            licensed_states=licensed_states,
            agent_id=customer.id,
            mapping_confirmed_at=now,
        )
        db.add(profile)
    else:
        profile.email = account.email
        profile.licensed_states = licensed_states
        profile.customer_id = customer.id
        profile.mapping_confirmed_at = now
    _audit(
        db,
        principal,
        "customer_user_account_replaced",
        "customer",
        customer.id,
        payload.reason,
        {"newAuthUserId": str(account.auth_user_id)},
    )
    db.commit()
    return {
        "customerId": customer.id,
        "authUserId": str(account.auth_user_id),
        "email": account.email,
        "licensedStates": licensed_states,
    }


@app.post("/api/admin/user-accounts/sync")
@app.post("/api/admin/recipients/sync", include_in_schema=False)
async def sync_user_accounts(
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    customers = {
        customer.slug: customer
        for customer in db.scalars(
            select(Customer).where(
                Customer.active.is_(True),
                Customer.deleted_at.is_(None),
            )
        )
        if customer.agency is None
        or (
            customer.agency.active
            and customer.agency.deleted_at is None
        )
    }
    seen = created = proposed = 0
    page = 1
    while True:
        data = await _supabase_admin(settings, "GET", f"/auth/v1/admin/users?page={page}&per_page=1000")
        users = data.get("users") or []
        if not users:
            break
        for user in users:
            role = str((user.get("app_metadata") or {}).get("jawnix_role") or "customer")
            if role != "customer":
                continue
            seen += 1
            user_id = uuid.UUID(str(user["id"]))
            email = str(user.get("email") or "").strip().lower()
            metadata = user.get("user_metadata") or {}
            profile = db.get(CustomerProfile, user_id)
            is_new = profile is None
            if profile is None:
                profile = CustomerProfile(
                    user_id=user_id,
                    email=email,
                    first_name=str(metadata.get("first_name") or ""),
                    last_name=str(metadata.get("last_name") or ""),
                    licensed_states=[],
                )
                db.add(profile)
                created += 1
            else:
                profile.email = email
            if profile.customer_id is None:
                candidates = {
                    re.sub(r"[^a-z0-9]+", "-", email.split("@", 1)[0]).strip("-"),
                    re.sub(r"[^a-z0-9]+", "-", profile.first_name.lower()).strip("-"),
                    re.sub(r"[^a-z0-9]+", "-", f"{profile.first_name} {profile.last_name}".lower()).strip("-"),
                }
                match = next(
                    (
                        customers[value]
                        for value in candidates
                        if value in customers
                    ),
                    None,
                )
                if match:
                    profile.customer_id = match.id
                    profile.mapping_confirmed_at = None
                    proposed += 1
            if is_new:
                profile.mapping_confirmed_at = None
        if len(users) < 1000:
            break
        page += 1
    db.commit()
    return {"seen": seen, "created": created, "proposedMappings": proposed, "allMappingsRequireConfirmation": True}


@app.patch("/api/admin/user-accounts/{user_id}/customer")
@app.patch("/api/admin/recipients/{user_id}", include_in_schema=False)
def map_user_account_customer(
    user_id: uuid.UUID,
    payload: CustomerMappingUpdate,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    profile = db.get(CustomerProfile, user_id)
    customer = db.get(Customer, payload.customer_id)
    if profile is None or customer is None:
        raise HTTPException(
            status_code=404,
            detail="User Account or Customer was not found.",
        )
    if (
        not customer.active
        or customer.deleted_at is not None
        or (
            customer.agency is not None
            and (
                not customer.agency.active
                or customer.agency.deleted_at is not None
            )
        )
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "User Accounts can only be mapped to an active Customer "
                "and Agency."
            ),
        )
    profile.customer_id = customer.id
    profile.mapping_confirmed_at = datetime.now(timezone.utc) if payload.confirmed else None
    db.commit()
    return {"ok": True}


@app.patch("/api/admin/agencies/{agency_id}")
def update_agency(
    agency_id: int,
    payload: AgencyUpdate,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    agency = db.get(Agency, agency_id)
    if agency is None or agency.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Agency was not found.")
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Agency name is required.")
    agency.name = name
    agency.active = payload.active
    _audit(
        db,
        principal,
        "agency_updated",
        "agency",
        agency.id,
        payload.reason,
        {"active": agency.active},
    )
    db.commit()
    return {"ok": True}


@app.patch("/api/admin/customers/{customer_id}")
@app.patch("/api/admin/agents/{customer_id}", include_in_schema=False)
def update_customer(
    customer_id: int,
    payload: CustomerUpdate,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    customer = db.get(Customer, customer_id)
    if customer is None or customer.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Customer was not found.")
    agency = db.get(Agency, payload.agency_id) if payload.agency_id is not None else None
    if payload.agency_id is not None and (agency is None or agency.deleted_at is not None):
        raise HTTPException(status_code=404, detail="Agency was not found.")
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Customer name is required.")
    customer.name = name
    customer.agency_id = agency.id if agency else None
    customer.active = payload.active
    _audit(
        db,
        principal,
        "customer_updated",
        "customer",
        customer.id,
        payload.reason,
        {
            "active": customer.active,
            "agencyId": customer.agency_id,
        },
    )
    db.commit()
    return {"ok": True}


def _customer_dependency_counts(db: Session, customer_id: int) -> dict:
    return {
        "requests": int(
            db.scalar(
                select(func.count(LeadRequest.id)).where(
                    LeadRequest.agent_id == customer_id
                )
            )
            or 0
        ),
        "distributions": int(
            db.scalar(
                select(func.count(DistributionEvent.id)).where(
                    DistributionEvent.agent_id == customer_id
                )
            )
            or 0
        ),
        "outcomes": int(
            db.scalar(
                select(func.count(LeadOutcome.id)).where(
                    LeadOutcome.customer_id == customer_id
                )
            )
            or 0
        ),
        "reports": int(
            db.scalar(
                select(func.count(LeadReport.id)).where(
                    LeadReport.customer_id == customer_id
                )
            )
            or 0
        ),
        "profiles": int(
            db.scalar(
                select(func.count(CustomerProfile.user_id)).where(
                    CustomerProfile.agent_id == customer_id
                )
            )
            or 0
        ),
        "userAccounts": int(
            db.scalar(
                select(func.count(UserAccount.auth_user_id)).where(
                    UserAccount.customer_id == customer_id
                )
            )
            or 0
        ),
    }


@app.delete("/api/admin/customers/{customer_id}")
def delete_customer(
    customer_id: int,
    payload: CustomerDelete,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    customer = db.scalar(
        select(Customer)
        .where(Customer.id == customer_id)
        .with_for_update()
    )
    if customer is None or customer.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Customer was not found.")
    if payload.confirm_slug != customer.slug:
        raise HTTPException(
            status_code=409,
            detail="Customer slug confirmation did not match.",
        )
    if customer.active:
        raise HTTPException(
            status_code=409,
            detail="Deactivate the Customer before deletion.",
        )
    dependencies = _customer_dependency_counts(db, customer.id)
    if payload.hard_delete:
        if any(dependencies.values()):
            raise HTTPException(
                status_code=409,
                detail={
                    "message": (
                        "Hard deletion is blocked while Customer "
                        "dependencies exist."
                    ),
                    "dependencies": dependencies,
                },
            )
        _audit(
            db,
            principal,
            "customer_hard_deleted",
            "customer",
            customer.id,
            payload.reason,
            {"slug": customer.slug},
        )
        db.delete(customer)
        db.commit()
        return {"ok": True, "hardDeleted": True}
    for profile in db.scalars(
        select(CustomerProfile).where(
            CustomerProfile.agent_id == customer.id
        )
    ):
        profile.customer_id = None
        profile.mapping_confirmed_at = None
    for account in db.scalars(
        select(UserAccount).where(
            UserAccount.customer_id == customer.id
        )
    ):
        account.active = False
        account.replaced_at = utcnow()
    customer.deleted_at = utcnow()
    _audit(
        db,
        principal,
        "customer_deleted",
        "customer",
        customer.id,
        payload.reason,
        {"dependencies": dependencies, "historyPreserved": True},
    )
    db.commit()
    return {
        "ok": True,
        "hardDeleted": False,
        "historyPreserved": True,
    }


@app.post("/api/admin/customers/{customer_id}/erase")
def erase_customer_personal_data(
    customer_id: int,
    payload: ActionReason,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    customer = db.scalar(
        select(Customer)
        .where(Customer.id == customer_id)
        .with_for_update()
    )
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer was not found.")
    if customer.active:
        raise HTTPException(
            status_code=409,
            detail="Deactivate the Customer before personal-data erasure.",
        )
    tombstone = db.scalar(
        select(CustomerTombstone).where(
            CustomerTombstone.former_customer_id == customer.id
        )
    )
    if tombstone is not None:
        return {
            "customerId": customer.id,
            "tombstoneId": str(tombstone.id),
            "duplicate": True,
        }
    opaque_key = hashlib.sha256(
        f"{customer.id}:{uuid.uuid4()}".encode("utf-8")
    ).hexdigest()
    tombstone = CustomerTombstone(
        former_customer_id=customer.id,
        opaque_key=opaque_key,
        actor_id=str(principal.user_id),
        reason=payload.reason.strip(),
    )
    db.add(tombstone)
    anonymous_email = f"erased-{opaque_key[:20]}@invalid.local"
    for profile in db.scalars(
        select(CustomerProfile).where(
            CustomerProfile.agent_id == customer.id
        )
    ):
        profile.email = anonymous_email
        profile.first_name = ""
        profile.last_name = ""
        profile.phone = ""
        profile.licensed_states = []
        profile.mapping_confirmed_at = None
    for account in db.scalars(
        select(UserAccount).where(
            UserAccount.customer_id == customer.id
        )
    ):
        account.email = anonymous_email
        account.active = False
        account.replaced_at = utcnow()
    original_slug = customer.slug
    customer.name = "Deleted Customer"
    customer.slug = f"deleted-customer-{customer.id}"
    customer.licensed_states = []
    customer.deleted_at = customer.deleted_at or utcnow()
    _audit(
        db,
        principal,
        "customer_personal_data_erased",
        "customer_tombstone",
        tombstone.id,
        payload.reason,
        {
            "formerCustomerId": customer.id,
            "formerSlugHash": hashlib.sha256(
                original_slug.encode("utf-8")
            ).hexdigest(),
        },
    )
    db.commit()
    return {
        "customerId": customer.id,
        "tombstoneId": str(tombstone.id),
        "historyPreserved": True,
    }


@app.delete("/api/admin/agents/{agent_id}", include_in_schema=False)
def delete_agent(
    agent_id: int,
    payload: DeleteConfirmation,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    agent = db.get(Customer, agent_id)
    if agent is None or agent.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Agent was not found.")
    if payload.confirm_slug != agent.slug:
        raise HTTPException(status_code=409, detail="Agent slug confirmation did not match.")
    active_requests = int(
        db.scalar(
            select(func.count(LeadRequest.id)).where(
                LeadRequest.agent_id == agent.id,
                LeadRequest.status.notin_(
                    [
                        RequestStatus.delivered.value,
                        RequestStatus.rejected.value,
                        RequestStatus.canceled.value,
                    ]
                ),
            )
        )
        or 0
    )
    if active_requests:
        raise HTTPException(
            status_code=409,
            detail=f"Resolve {active_requests} active request(s) before deleting this agent.",
        )
    profiles = list(db.scalars(select(CustomerProfile).where(CustomerProfile.agent_id == agent.id)))
    for profile in profiles:
        profile.customer_id = None
        profile.mapping_confirmed_at = None
    agent.active = False
    agent.deleted_at = utcnow()
    db.commit()
    return {"ok": True, "unassignedRecipients": len(profiles), "historyPreserved": True}


@app.delete("/api/admin/agencies/{agency_id}")
def delete_agency(
    agency_id: int,
    payload: CustomerDelete,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    agency = db.get(Agency, agency_id)
    if agency is None or agency.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Agency was not found.")
    if payload.confirm_slug != agency.slug:
        raise HTTPException(status_code=409, detail="Agency slug confirmation did not match.")
    if agency.active:
        raise HTTPException(
            status_code=409,
            detail="Deactivate the Agency before deletion.",
        )
    customers = list(
        db.scalars(
            select(Customer).where(Customer.agency_id == agency.id)
        )
    )
    active_customers = [
        customer.slug
        for customer in customers
        if customer.active and customer.deleted_at is None
    ]
    if active_customers:
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    "Deactivate every Customer before deleting the Agency."
                ),
                "activeCustomers": active_customers,
            },
        )
    customer_ids = [customer.id for customer in customers]
    distribution_count = int(
        db.scalar(
            select(func.count(DistributionEvent.id)).where(
                DistributionEvent.agency_id == agency.id
            )
        )
        or 0
    )
    if payload.hard_delete:
        dependencies = {
            "customers": len(customers),
            "distributions": distribution_count,
        }
        if any(dependencies.values()):
            raise HTTPException(
                status_code=409,
                detail={
                    "message": (
                        "Hard deletion is blocked while Agency "
                        "dependencies exist."
                    ),
                    "dependencies": dependencies,
                },
            )
        _audit(
            db,
            principal,
            "agency_hard_deleted",
            "agency",
            agency.id,
            payload.reason,
            {"slug": agency.slug},
        )
        db.delete(agency)
        db.commit()
        return {"ok": True, "hardDeleted": True}
    active_requests = (
        int(
            db.scalar(
                select(func.count(LeadRequest.id)).where(
                    LeadRequest.agent_id.in_(customer_ids),
                    LeadRequest.status.notin_(
                        [
                            RequestStatus.delivered.value,
                            RequestStatus.rejected.value,
                            RequestStatus.canceled.value,
                        ]
                    ),
                )
            )
            or 0
        )
        if customer_ids
        else 0
    )
    if active_requests:
        raise HTTPException(
            status_code=409,
            detail=f"Resolve {active_requests} active request(s) before deleting this agency.",
        )
    profiles = (
        list(
            db.scalars(
                select(CustomerProfile).where(
                    CustomerProfile.agent_id.in_(customer_ids)
                )
            )
        )
        if customer_ids
        else []
    )
    for profile in profiles:
        profile.customer_id = None
        profile.mapping_confirmed_at = None
    now = utcnow()
    deleted_customers = 0
    for customer in customers:
        if customer.deleted_at is None:
            deleted_customers += 1
        customer.active = False
        customer.deleted_at = customer.deleted_at or now
    agency.active = False
    agency.deleted_at = now
    _audit(
        db,
        principal,
        "agency_deleted",
        "agency",
        agency.id,
        payload.reason,
        {
            "deletedCustomers": deleted_customers,
            "historyPreserved": True,
        },
    )
    db.commit()
    return {
        "ok": True,
        "deletedCustomers": deleted_customers,
        "unassignedRecipients": len(profiles),
        "historyPreserved": True,
    }


async def _supabase_admin(settings: Settings, method: str, path: str, payload: dict | None = None):
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise HTTPException(status_code=503, detail="Supabase administration is not configured.")
    headers = {
        "apikey": settings.supabase_service_role_key,
        "Authorization": f"Bearer {settings.supabase_service_role_key}",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.request(method, f"{settings.supabase_url.rstrip('/')}{path}", headers=headers, json=payload)
    if response.status_code >= 300:
        raise HTTPException(status_code=502, detail=f"Supabase administration failed: {response.text}")
    return response.json()


async def _send_password_reset(settings: Settings, email: str) -> None:
    if not settings.supabase_url or not settings.supabase_anon_key:
        raise HTTPException(status_code=503, detail="Supabase password recovery is not configured.")
    redirect_to = f"{settings.public_base_url.rstrip('/')}/portal-accept.html"
    headers = {
        "apikey": settings.supabase_anon_key,
        "Authorization": f"Bearer {settings.supabase_anon_key}",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{settings.supabase_url.rstrip('/')}/auth/v1/recover",
            params={"redirect_to": redirect_to},
            headers=headers,
            json={"email": email},
        )
    if response.status_code >= 300:
        raise HTTPException(status_code=502, detail=f"Supabase password recovery failed: {response.text}")


@app.post("/api/admin/user-accounts/{user_id}/send-password-reset")
@app.post(
    "/api/admin/recipients/{user_id}/send-password-reset",
    include_in_schema=False,
)
async def send_user_account_password_reset(
    user_id: uuid.UUID,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    profile = db.get(CustomerProfile, user_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Recipient was not found.")
    await _send_password_reset(settings, profile.email)
    return {"ok": True, "email": profile.email}


@app.post("/api/admin/customers", status_code=201)
async def create_customer(
    payload: CustomerCreate,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    created = await _supabase_admin(
        settings,
        "POST",
        "/auth/v1/admin/users",
        {
            "email": str(payload.email).lower(),
            "password": payload.password,
            "email_confirm": True,
            "user_metadata": {"first_name": payload.first_name, "last_name": payload.last_name},
            "app_metadata": {"jawnix_role": "customer"},
        },
    )
    profile = CustomerProfile(
        user_id=uuid.UUID(str(created["id"])),
        email=str(payload.email).lower(),
        first_name=payload.first_name.strip(),
        last_name=payload.last_name.strip(),
        licensed_states=[],
    )
    db.add(profile)
    db.commit()
    return {"ok": True, "userId": str(profile.user_id), "mappingConfirmed": False}


@app.post("/api/admin/requests/{request_id}/{action}")
def admin_request_action(
    request_id: uuid.UUID,
    action: str,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if action not in {"approve", "reject", "retry", "retry_delivery"}:
        raise HTTPException(status_code=404, detail="Unknown action.")
    try:
        item = transition_request(db, request_id, action)
    except TransitionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    db.commit()
    return _request_dict(item)


@app.post("/api/admin/requests/{request_id}/artifact/regenerate")
def regenerate_batch_artifact(
    request_id: uuid.UUID,
    payload: ActionReason,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    from pathlib import Path

    from .allocation import generate_artifact

    item = db.scalar(
        select(LeadRequest)
        .where(LeadRequest.id == request_id)
        .with_for_update()
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Request was not found.")
    events = list(
        db.scalars(
            select(DistributionEvent)
            .where(DistributionEvent.request_id == item.id)
            .order_by(DistributionEvent.id)
        )
    )
    if len(events) != item.lead_count:
        raise HTTPException(
            status_code=409,
            detail="Committed Distribution Events are incomplete.",
        )
    artifact = item.artifact
    now = utcnow()
    if (
        artifact is not None
        and Path(artifact.path).is_file()
        and artifact.expires_at is not None
        and artifact.expires_at > now
    ):
        raise HTTPException(
            status_code=409,
            detail="Batch Artifact has not expired.",
        )
    artifact = generate_artifact(db, item, events, settings)
    _audit(
        db,
        principal,
        "batch_artifact_regenerated",
        "batch_artifact",
        artifact.id,
        payload.reason,
        {
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


@app.post("/api/admin/inventory-conflicts/{conflict_id}/{action}")
def admin_inventory_conflict_action(
    conflict_id: uuid.UUID,
    action: str,
    payload: ActionReason,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    from .allocation import decide_inventory_conflict

    try:
        result = decide_inventory_conflict(
            db,
            conflict_id,
            action,
            str(principal.user_id),
            payload.reason,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    db.commit()
    return result


@app.post("/api/integrations/telegram/webhook")
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
            action, target_id = parse_anomaly_callback_data(
                callback_value
            )
            target_kind = "anomaly"
        except ValueError:
            try:
                action, target_id = parse_conflict_callback_data(
                    callback_value
                )
                target_kind = "conflict"
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail="Malformed Telegram callback.",
                ) from None

    telegram = TelegramClient(settings)
    if user_id not in settings.telegram_approvers or chat_id != settings.telegram_chat_id:
        background_tasks.add_task(telegram.answer_callback, callback_query_id, "Not authorized")
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
    else:
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
    db.commit()
    background_tasks.add_task(telegram.answer_callback, callback_query_id, "Queued")
    return {"ok": True, "queued": True}


@app.post("/api/integrations/resend/webhook")
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
                enqueue_job(db, "update_notification", item.id)
    db.commit()
    return {"ok": True}
