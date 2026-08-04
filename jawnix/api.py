from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from svix.webhooks import Webhook, WebhookVerificationError

from .activity import ACTIVITY_PAGE_SIZE, query_activity, record_activity
from .agency_management import (
    AgencyAssignmentConflict,
    agency_details,
    agency_directory,
    assign_customer,
    assignment_preview,
)
from .auth import Principal, clear_session, issue_session, require_admin, require_principal, verify_supabase_token
from .admin_mfa_api import router as admin_mfa_router
from .billing import (
    BillingError,
    close_credit_purchase_for_session,
    complete_credit_purchase_for_session,
    configure_customer_billing,
    place_batch_hold,
    post_admin_adjustment,
    prepare_submission,
    start_credit_purchase,
    wallet_view,
)
from .config import Settings, get_settings
from .stripe_client import StripeClientError, get_stripe_client
from .customer_accounts import (
    ProvisionResult,
    UserAccountConflict,
    accept_user_account_invitation,
    cancel_user_account_invitation,
    invite_user_account,
    pending_invitation,
)
from .customer_directory import (
    build_customer_details,
    build_customer_directory,
    customer_dependency_counts,
)
from .customer_overview import build_customer_overview
from .customer_requests import (
    RequestSubmissionError,
    build_request_workspace,
    cancel_request as withdraw_request,
    request_detail,
    submit_request,
)
from .database import get_db
from .eligibility import (
    RESTORE_NOTICE,
    ControlConflict,
    correct_from_report,
    describe_report,
    dismiss_report,
    lead_evidence,
    record_correction,
    suppress_from_report,
)
from .feedback import apply_disposition_controls
from .exclusions import (
    EXCLUSION_TYPES,
    ExclusionDecisionError,
    decide_exclusion_list,
    exclusion_list_status,
)
from .frontend import register_frontend_shell
from .jobs import enqueue_job
from .licensed_states import (
    LicensedStateApplyResult,
    LicensedStateConflict,
    LicensedStateConfirmation,
    LicensedStateReview,
    LicensedStateReviewError,
    LicensedStateSelection,
    LicensedStateWorkspace,
    apply_review as apply_licensed_state_review,
    preview as preview_licensed_states,
    workspace as licensed_state_workspace,
)
from .milestone_emails import enqueue_milestone_email
from .niche_policy import (
    NichePolicyError,
    load_policy,
    normalize_policy_rows,
    replace_policy,
    unmapped_inventory_query,
    upload_status as niche_assignment_upload_status,
)
from .recommendations import (
    RecommendationDecisionError,
    decide_recommendation,
)
from .models import (
    Agency,
    AgencyMembershipHistory,
    AdminMFAState,
    Customer,
    BatchArtifact,
    CustomerProfile,
    CustomerTombstone,
    DailySourcePerformance,
    DistributionEvent,
    EligibilityHold,
    ExclusionList,
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
    NicheAssignmentUpload,
    PerformanceSuggestionNote,
    RequestStatus,
    ScrapeSegmentResult,
    ScraperConfiguration,
    ScrapeAnomaly,
    ScraperRun,
    SourceRecommendation,
    SourceNicheMapping,
    SourceSegment,
    UserAccount,
    UserAccountInvitation,
    WebhookReceipt,
    utcnow,
)
from .operations_overview import OperationsOverview, operations_overview
from .mfa_provider import MFAProviderError, get_mfa_provider
from .schemas import (
    AgencyAssignment,
    AgencyCreate,
    AgencyUpdate,
    ActionReason,
    CreditAdjustmentCreate,
    CreditPurchaseCreate,
    CustomerBillingUpdate,
    CooldownWindowUpdate,
    LeadCorrectionApply,
    LeadReportCreate,
    LeadReportCorrect,
    LeadReportNote,
    NightlyDeliveryReconcile,
    CustomerUpdate,
    CustomerCreate,
    CustomerDelete,
    NichePolicyDraft,
    NichePolicyUpdate,
    CustomerDetailsOut,
    CustomerDirectoryOut,
    DeleteConfirmation,
    FeedbackCreate,
    FeedbackLookup,
    FeedbackSearch,
    OutcomeCreate,
    OutcomeOut,
    ProfileOut,
    ProfileUpdate,
    CustomerMappingUpdate,
    CustomerOverviewOut,
    CustomerRequestCreate,
    CustomerRequestDetail,
    CustomerRequestReceipt,
    CustomerRequestWorkspaceOut,
    RecommendationDecision,
    RequestCreate,
    RequestOut,
    SessionExchange,
    ScraperConfigurationCreate,
    SourceNicheDecision,
    UserAccountInvite,
    UserAccountReplace,
)
from .scraper_proxy import (
    accept_scraper_handoff,
    clear_scraper_session,
    forward_scraper_request,
    native_router as native_scraper_router,
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
    parse_exclusion_callback_data,
    parse_recommendation_callback_data,
    verify_telegram_secret,
)
from .fulfillment import (
    TRANSITION_ACTIONS,
    artifact_available,
    describe_conflict,
    describe_request,
    workspace as fulfillment_workspace,
)
from .transitions import TransitionError, transition_request


MAX_CSV_UPLOAD_BYTES = 10 * 1024 * 1024
_UPLOAD_CHUNK_BYTES = 1024 * 1024


class UploadTooLargeError(ValueError):
    pass


log = logging.getLogger("jawnix.api")

app = FastAPI(title="Jawnix VPS API", version="1.0.0")
app.include_router(admin_mfa_router)
app.include_router(native_scraper_router)


@app.exception_handler(RequestValidationError)
async def redact_sensitive_validation_error(
    request: Request,
    exc: RequestValidationError,
):
    # FastAPI's default 422 body includes rejected input.  That is useful for
    # ordinary forms but would echo provider bearer tokens or TOTP codes from
    # this security surface.
    if (
        request.url.path.startswith("/api/auth/admin-mfa")
        or request.url.path == "/api/admin/scraper/step-up"
    ):
        return JSONResponse(
            status_code=422,
            content={"detail": "The administrator verification request was invalid."},
        )
    if request.url.path == "/api/admin/customers":
        return JSONResponse(
            status_code=422,
            content={"detail": "The Customer invitation request was invalid."},
        )
    return await request_validation_exception_handler(request, exc)

# The React shell at /app is the only UI (legacy static pages retired in P8).
register_frontend_shell(app)


@app.get(
    "/api/admin/operations-overview",
    response_model=OperationsOverview,
)
def admin_operations_overview(
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """One resilient aggregate for the administrator Operations overview."""
    return operations_overview(db)


@app.get("/api/admin/activity")
def admin_activity(
    q: str | None = None,
    actor: str | None = None,
    action: str | None = None,
    entity_type: str | None = Query(default=None, alias="entityType"),
    entity_id: str | None = Query(default=None, alias="entityId"),
    date_from: date | None = Query(default=None, alias="dateFrom"),
    date_to: date | None = Query(default=None, alias="dateTo"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(
        default=ACTIVITY_PAGE_SIZE,
        alias="pageSize",
        ge=1,
        le=100,
    ),
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Global administrator Activity, filtered and paginated on the server."""
    return query_activity(
        db,
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=page_size,
        query=q,
    )


@app.get("/api/admin/activity/{entity_type}/{entity_id}")
def admin_entity_activity(
    entity_type: str,
    entity_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(
        default=ACTIVITY_PAGE_SIZE,
        alias="pageSize",
        ge=1,
        le=100,
    ),
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """One entity timeline through the same query as global Activity."""
    return query_activity(
        db,
        entity_type=entity_type,
        entity_id=entity_id,
        page=page,
        page_size=page_size,
    )


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
    db: Session = Depends(get_db),
):
    if request_is_scraper_origin(request, settings):
        principal = scraper_principal_from_request(request, settings)
    else:
        principal = await require_admin(
            require_principal(request, settings),
            settings,
            db,
        )
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
    session_kwargs: dict = {}
    admin_next = "/app/admin/overview"
    if role == "admin":
        state = db.get(AdminMFAState, uuid.UUID(str(user["id"])))
        if state is None:
            state = AdminMFAState(
                user_id=uuid.UUID(str(user["id"])),
                session_generation=1,
            )
            db.add(state)
            db.flush()
        try:
            factors = await get_mfa_provider(settings).list_factors(
                state.user_id
            )
        except MFAProviderError:
            raise HTTPException(
                status_code=503,
                detail="Administrator verification is temporarily unavailable.",
            ) from None
        verified = [factor for factor in factors if factor.verified_totp]
        assurance = str(
            (user.get("_jawnix_auth_claims") or {}).get("aal") or "aal1"
        )
        factor_id = None
        if assurance == "aal2" and verified:
            factor_id = max(
                verified,
                key=lambda factor: (
                    factor.last_challenged_at
                    or factor.updated_at
                    or factor.created_at
                    or datetime.min.replace(tzinfo=timezone.utc)
                ),
            ).id
        session_kwargs = {
            "assurance": assurance,
            "session_generation": state.session_generation,
            "factor_id": factor_id,
        }
        if len(verified) < 2:
            admin_next = "/app/admin/mfa/enroll"
        elif assurance != "aal2":
            admin_next = "/app/admin/mfa/challenge"
        else:
            requested = payload.requested_next or ""
            admin_next = (
                requested
                if requested.startswith("/app/admin/")
                and not requested.startswith("//")
                else "/app/admin/overview"
            )
    principal = issue_session(
        response,
        user,
        settings,
        **session_kwargs,
    )
    if principal.role != "admin":
        # Signing in is what acceptance means: the invited person has proved
        # they hold the invited identity. Do this before the replaced-account
        # guard below, so an invitation that has just been accepted is not
        # mistaken for access that was taken away.
        try:
            accept_user_account_invitation(
                db,
                auth_user_id=principal.user_id,
                email=principal.email,
            )
        except UserAccountConflict as conflict:
            db.rollback()
            raise HTTPException(
                status_code=409,
                detail=conflict.message,
            ) from None
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
    if principal.role == "admin":
        next_path = admin_next
    else:
        requested = payload.requested_next or ""
        next_path = (
            requested
            if requested.startswith("/app/")
            and not requested.startswith("/app/admin/")
            and not requested.startswith("//")
            else "/app/overview"
        )
    return {
        "ok": True,
        "role": principal.role,
        "assurance": principal.assurance,
        "next": next_path,
    }


@app.post("/api/auth/logout")
def logout(
    response: Response,
    _: Principal = Depends(require_principal),
    settings: Settings = Depends(get_settings),
):
    clear_session(response, settings)
    return {"ok": True}


def _current_customer_profile(
    db: Session,
    principal: Principal,
) -> CustomerProfile:
    """The signed-in Customer's profile, or the reason there is none.

    Every Customer screen crosses this: a replaced User Account is refused
    before it can read or write anything under its old identity.
    """

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


def _mapped_customer_profile(
    db: Session,
    principal: Principal,
) -> CustomerProfile:
    profile = _current_customer_profile(db, principal)
    if profile.customer_id is None:
        raise HTTPException(status_code=404, detail="Customer was not found.")
    return profile


def _store_exclusion_upload(
    upload: UploadFile,
    *,
    exclusion_list_id: uuid.UUID,
    settings: Settings,
) -> Path:
    directory = Path(settings.batch_dir) / "exclusion-lists"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{exclusion_list_id}.csv"
    _store_bounded_upload(upload, path)
    return path


def _store_bounded_upload(upload: UploadFile, path: Path) -> None:
    written = 0
    try:
        with path.open("xb") as stream:
            while chunk := upload.file.read(_UPLOAD_CHUNK_BYTES):
                written += len(chunk)
                if written > MAX_CSV_UPLOAD_BYTES:
                    raise UploadTooLargeError
                stream.write(chunk)
    except Exception:
        path.unlink(missing_ok=True)
        raise


@app.post("/api/me/exclusion-lists", status_code=202)
def upload_customer_exclusion_list(
    file: UploadFile = File(...),
    exclusion_type: str = Form(..., alias="type"),
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    profile = _mapped_customer_profile(db, principal)
    normalized_type = exclusion_type.strip().casefold()
    if normalized_type not in EXCLUSION_TYPES:
        raise HTTPException(status_code=422, detail="Unknown Exclusion List type.")
    if not file.filename or not file.filename.casefold().endswith(".csv"):
        raise HTTPException(status_code=422, detail="Upload a CSV file.")
    item = ExclusionList(
        customer_id=profile.customer_id,
        uploaded_by=str(principal.user_id),
        exclusion_type=normalized_type,
        filename=Path(file.filename).name,
        storage_path="",
    )
    db.add(item)
    db.flush()
    try:
        item.storage_path = str(
            _store_exclusion_upload(
                file, exclusion_list_id=item.id, settings=settings
            )
        )
    except UploadTooLargeError:
        db.rollback()
        raise HTTPException(
            status_code=413,
            detail="CSV uploads must be 10 MB or smaller.",
        ) from None
    except OSError as exc:
        db.rollback()
        raise HTTPException(
            status_code=503,
            detail="The Exclusion List could not be stored.",
        ) from exc
    enqueue_job(
        db,
        "ingest_exclusion_list",
        payload={"exclusion_list_id": str(item.id)},
    )
    record_activity(
        db,
        action="exclusion_list_uploaded",
        target_type="exclusion_list",
        target_id=item.id,
        actor_id=principal.user_id,
        reason="Customer uploaded an Exclusion List",
        details={
            "customerId": profile.customer_id,
            "type": item.exclusion_type,
            "filename": item.filename,
            "global": False,
        },
    )
    db.commit()
    return exclusion_list_status(item, db)


@app.get("/api/me/exclusion-lists/{exclusion_list_id}")
def customer_exclusion_list_status(
    exclusion_list_id: uuid.UUID,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
):
    profile = _mapped_customer_profile(db, principal)
    item = db.scalar(
        select(ExclusionList).where(
            ExclusionList.id == exclusion_list_id,
            ExclusionList.customer_id == profile.customer_id,
        )
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Exclusion List was not found.")
    return exclusion_list_status(item, db)


@app.get("/api/me/exclusion-lists")
def list_customer_exclusion_lists(
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
):
    profile = _mapped_customer_profile(db, principal)
    return [
        exclusion_list_status(item, db)
        for item in db.scalars(
            select(ExclusionList)
            .where(ExclusionList.customer_id == profile.customer_id)
            .order_by(ExclusionList.created_at.desc(), ExclusionList.id)
        )
    ]


@app.post("/api/admin/exclusion-lists", status_code=202)
def upload_admin_exclusion_list(
    file: UploadFile = File(...),
    exclusion_type: str = Form(..., alias="type"),
    reason: str = Form(...),
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    normalized_type = exclusion_type.strip().casefold()
    normalized_reason = reason.strip()
    if normalized_type not in EXCLUSION_TYPES:
        raise HTTPException(status_code=422, detail="Unknown Exclusion List type.")
    if not normalized_reason:
        raise HTTPException(status_code=422, detail="An upload reason is required.")
    if not file.filename or not file.filename.casefold().endswith(".csv"):
        raise HTTPException(status_code=422, detail="Upload a CSV file.")
    item = ExclusionList(
        customer_id=None,
        uploaded_by=str(principal.user_id),
        exclusion_type=normalized_type,
        filename=Path(file.filename).name,
        storage_path="",
        decision_reason=normalized_reason,
    )
    db.add(item)
    db.flush()
    try:
        item.storage_path = str(
            _store_exclusion_upload(
                file, exclusion_list_id=item.id, settings=settings
            )
        )
    except UploadTooLargeError:
        db.rollback()
        raise HTTPException(
            status_code=413,
            detail="CSV uploads must be 10 MB or smaller.",
        ) from None
    except OSError as exc:
        db.rollback()
        raise HTTPException(
            status_code=503,
            detail="The Exclusion List could not be stored.",
        ) from exc
    enqueue_job(
        db,
        "ingest_exclusion_list",
        payload={"exclusion_list_id": str(item.id)},
    )
    record_activity(
        db,
        action="exclusion_list_uploaded",
        target_type="exclusion_list",
        target_id=item.id,
        actor_id=principal.user_id,
        reason=normalized_reason,
        details={
            "customerId": None,
            "type": item.exclusion_type,
            "filename": item.filename,
            "globalOnIngestion": True,
        },
    )
    db.commit()
    return exclusion_list_status(item, db)


@app.get("/api/admin/exclusion-lists/{exclusion_list_id}")
def admin_exclusion_list_status(
    exclusion_list_id: uuid.UUID,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    item = db.get(ExclusionList, exclusion_list_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Exclusion List was not found.")
    return exclusion_list_status(item, db)


@app.get("/api/admin/customers/{customer_id}/exclusion-lists")
def admin_customer_exclusion_lists(
    customer_id: int,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    customer = db.get(Customer, customer_id)
    if customer is None or customer.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Customer was not found.")
    return [
        exclusion_list_status(item, db)
        for item in db.scalars(
            select(ExclusionList)
            .where(ExclusionList.customer_id == customer_id)
            .order_by(ExclusionList.created_at.desc(), ExclusionList.id)
        )
    ]


@app.post("/api/admin/exclusion-lists/{exclusion_list_id}/{action}")
def admin_decide_exclusion_list(
    exclusion_list_id: uuid.UUID,
    action: str,
    payload: ActionReason,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    try:
        item = decide_exclusion_list(
            db,
            exclusion_list_id,
            action,
            actor_id=principal.user_id,
            reason=payload.reason,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except ExclusionDecisionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    db.commit()
    return exclusion_list_status(item, db)


@app.get("/api/me/profile", response_model=ProfileOut)
def get_profile(principal: Principal = Depends(require_principal), db: Session = Depends(get_db)):
    return _current_customer_profile(db, principal)


@app.get("/api/me/billing")
def get_my_billing(
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
):
    profile = _current_customer_profile(db, principal)
    if profile.customer is None:
        raise HTTPException(status_code=404, detail="Customer was not found.")
    return wallet_view(db, profile.customer)


@app.post("/api/me/billing/purchases", status_code=201)
def create_credit_purchase(
    payload: CreditPurchaseCreate,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    profile = _current_customer_profile(db, principal)
    customer = profile.customer
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer was not found.")
    stripe = get_stripe_client(settings)
    base = settings.public_base_url.rstrip("/")
    try:
        purchase, checkout_url = start_credit_purchase(
            db,
            customer=customer,
            amount_dollars=payload.amount_dollars,
            customer_email=profile.email,
            stripe=stripe,
            success_url=f"{base}/app/account?purchase=success",
            cancel_url=f"{base}/app/account?purchase=cancelled",
        )
    except BillingError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
        ) from None
    db.commit()
    wallet = wallet_view(db, customer)
    purchase_view = next(
        item for item in wallet["purchases"] if item["id"] == str(purchase.id)
    )
    return {
        "checkoutUrl": checkout_url,
        "purchase": purchase_view,
    }


@app.post("/api/billing/stripe/webhook")
async def stripe_billing_webhook(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    if not settings.stripe_webhook_secret and settings.stripe_client is None:
        raise HTTPException(
            status_code=503,
            detail="Stripe webhook verification is not configured.",
        )
    body = await request.body()
    signature = request.headers.get("Stripe-Signature", "")
    stripe = get_stripe_client(settings)
    try:
        event = stripe.verify_webhook_signature(body, signature)
    except (ValueError, StripeClientError):
        raise HTTPException(
            status_code=401,
            detail="Invalid Stripe webhook signature.",
        ) from None
    event_id = str(event.get("id") or "").strip()
    if not event_id:
        raise HTTPException(status_code=400, detail="Stripe event id is required.")
    try:
        with db.begin_nested():
            db.add(WebhookReceipt(provider="stripe", event_key=event_id))
            db.flush()
    except IntegrityError:
        return {"ok": True, "duplicate": True}

    event_type = str(event.get("type") or "")
    handled_types = {
        "checkout.session.completed",
        "checkout.session.async_payment_succeeded",
        "checkout.session.async_payment_failed",
        "checkout.session.expired",
    }
    if event_type not in handled_types:
        db.commit()
        return {"ok": True, "ignored": True}

    session_payload = (event.get("data") or {}).get("object") or {}
    checkout_session_id = str(session_payload.get("id") or "").strip()
    if not checkout_session_id:
        db.commit()
        return {"ok": True, "ignored": True}

    payment_status = str(session_payload.get("payment_status") or "")
    should_credit = (
        event_type == "checkout.session.async_payment_succeeded"
        or (
            event_type == "checkout.session.completed"
            and payment_status == "paid"
        )
    )
    try:
        if should_credit:
            purchase = complete_credit_purchase_for_session(
                db,
                stripe_checkout_session_id=checkout_session_id,
                settled_late=(
                    event_type == "checkout.session.async_payment_succeeded"
                ),
            )
        elif event_type in {
            "checkout.session.async_payment_failed",
            "checkout.session.expired",
        }:
            purchase = close_credit_purchase_for_session(
                db,
                stripe_checkout_session_id=checkout_session_id,
                status=(
                    "failed"
                    if event_type == "checkout.session.async_payment_failed"
                    else "expired"
                ),
            )
        else:
            db.commit()
            return {"ok": True, "pending": True}
    except BillingError as exc:
        # Stripe retries a non-2xx, so a refusal here repeats until someone
        # looks. Say which session and why in the log rather than leaving a
        # silent loop.
        log.warning(
            "Stripe %s for session %s was refused: %s",
            event_type,
            checkout_session_id,
            exc.detail,
        )
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
        ) from None
    if purchase is None:
        db.commit()
        return {"ok": True, "ignored": True}
    db.commit()
    return {"ok": True}


@app.get("/api/me/licensed-states", response_model=LicensedStateWorkspace)
def get_licensed_state_workspace(
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
):
    return licensed_state_workspace(_current_customer_profile(db, principal))


@app.post(
    "/api/me/licensed-states/preview",
    response_model=LicensedStateReview,
)
def preview_licensed_state_changes(
    payload: LicensedStateSelection,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    try:
        return preview_licensed_states(
            db,
            user_id=principal.user_id,
            profile=_current_customer_profile(db, principal),
            selection=payload,
            settings=settings,
        )
    except LicensedStateConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except LicensedStateReviewError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@app.post(
    "/api/me/licensed-states/apply",
    response_model=LicensedStateApplyResult,
)
def apply_licensed_state_changes(
    payload: LicensedStateConfirmation,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    # Refuse replaced accounts before validating even a signed review.
    _current_customer_profile(db, principal)
    try:
        return apply_licensed_state_review(
            db,
            user_id=principal.user_id,
            confirmation=payload,
            settings=settings,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except LicensedStateConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except LicensedStateReviewError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@app.get("/api/me/overview", response_model=CustomerOverviewOut)
def get_customer_overview(
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
):
    return build_customer_overview(
        db,
        user_id=principal.user_id,
        profile=_current_customer_profile(db, principal),
    )


@app.get("/api/me/batch-requests", response_model=CustomerRequestWorkspaceOut)
def get_batch_request_workspace(
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
):
    """Everything the guided Batch Request screen needs, in one read."""

    return build_request_workspace(
        db,
        user_id=principal.user_id,
        profile=_current_customer_profile(db, principal),
    )


@app.post(
    "/api/me/batch-requests",
    response_model=CustomerRequestReceipt,
    status_code=201,
    responses={
        200: {
            "model": CustomerRequestReceipt,
            "description": "This submission key already created a Batch Request.",
        }
    },
)
def submit_batch_request(
    payload: CustomerRequestCreate,
    response: Response,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
):
    """Submit the reviewed request, at most once per submission key.

    A replay answers 200 with the request the first attempt created, so a
    double-click or a retried POST is indistinguishable from a single one.
    """

    profile = _current_customer_profile(db, principal)
    try:
        item, created = submit_request(
            db,
            user_id=principal.user_id,
            profile=profile,
            payload=payload,
        )
    except RequestSubmissionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    if not created:
        response.status_code = 200
    return CustomerRequestReceipt(created=created, request=request_detail(item))


@app.post(
    "/api/me/batch-requests/{request_id}/cancel",
    response_model=CustomerRequestDetail,
)
def cancel_batch_request(
    request_id: uuid.UUID,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
):
    """Withdraw a Batch Request and return its updated milestone graph."""

    # Called for its refusals: a replaced User Account may not withdraw work
    # belonging to the identity it replaced.
    _current_customer_profile(db, principal)
    try:
        item = withdraw_request(
            db,
            user_id=principal.user_id,
            request_id=request_id,
        )
    except RequestSubmissionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    return request_detail(item)


@app.get("/api/me/batch-requests/{request_id}/artifact")
def download_batch_artifact(
    request_id: uuid.UUID,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
):
    """Download this Customer's live artifact and record the retrieval."""

    if principal.role != "customer" or principal.audience != "customer":
        raise HTTPException(status_code=403, detail="Customer access required.")
    profile = _current_customer_profile(db, principal)
    item = db.scalar(
        select(LeadRequest).where(
            LeadRequest.id == request_id,
            LeadRequest.agent_id == profile.customer_id,
            LeadRequest.status == RequestStatus.delivered.value,
        )
    )
    # A missing request, another Customer's request, and a request that has not
    # been delivered are intentionally indistinguishable at this boundary.
    if item is None or item.artifact is None:
        raise HTTPException(status_code=404, detail="Batch Artifact was not found.")
    artifact = item.artifact
    if not artifact_available(artifact):
        raise HTTPException(
            status_code=410,
            detail="Batch Artifact has expired. Contact Jawnix to regenerate it.",
        )

    record_activity(
        db,
        action="batch_artifact_downloaded",
        target_type="batch_request",
        target_id=item.id,
        actor_id=principal.user_id,
        reason="Customer downloaded the live Batch Artifact.",
        details={
            "artifactId": artifact.id,
            "requestId": str(item.id),
            "filename": artifact.filename,
            "rowCount": artifact.row_count,
            "parts": artifact.parts,
            "expiresAt": (
                artifact.expires_at.isoformat()
                if artifact.expires_at is not None
                else None
            ),
        },
    )
    db.commit()
    return FileResponse(
        artifact.path,
        media_type=(
            "application/zip"
            if artifact.filename.lower().endswith(".zip")
            else "text/csv"
        ),
        filename=artifact.filename,
        content_disposition_type="attachment",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


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
    if next_states != previous_states:
        raise HTTPException(
            status_code=409,
            detail=(
                "Licensed State changes require an impact review. "
                "Use Account to review and confirm them."
            ),
        )

    profile.first_name = payload.first_name.strip()
    profile.last_name = payload.last_name.strip()
    profile.phone = payload.phone.strip()
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


@app.get("/api/me/feedback/dispositions")
def feedback_disposition_catalog(
    _: Principal = Depends(require_principal),
):
    """The Lead Dispositions a Customer chooses from, and what each one does.

    Served rather than duplicated in the client so the consequence a Customer
    reads is derived from the rule that materializes it. A Lead Report and an
    Eligibility Hold cannot be undone from the Customer's side; copy that
    drifted from `apply_disposition_controls` would misstate an irreversible
    effect.
    """
    from .dispositions import catalog_payload

    return catalog_payload()


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


@app.post("/api/me/feedback/search")
def search_feedback(
    payload: FeedbackSearch,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
):
    profile = db.get(CustomerProfile, principal.user_id)
    if profile is None or profile.customer_id is None:
        return []
    query = payload.query
    digits = "".join(character for character in query if character.isdigit())
    conditions = [
        DistributionEvent.title.icontains(query, autoescape=True),
    ]
    if digits:
        conditions.append(
            DistributionEvent.phone.contains(digits, autoescape=True)
        )
    events = list(
        db.scalars(
            select(DistributionEvent)
            .join(
                LeadRequest,
                LeadRequest.id == DistributionEvent.request_id,
            )
            .where(
                DistributionEvent.agent_id == profile.customer_id,
                LeadRequest.agent_id == profile.customer_id,
                LeadRequest.user_id == principal.user_id,
                LeadRequest.status == RequestStatus.delivered.value,
                or_(*conditions),
            )
            .order_by(
                DistributionEvent.delivered_at.desc(),
                DistributionEvent.id.desc(),
            )
            .limit(20)
        )
    )
    states = {
        state.distribution_event_id: state.current_disposition
        for state in db.scalars(
            select(LeadDispositionState).where(
                LeadDispositionState.distribution_event_id.in_(
                    [event.id for event in events]
                )
            )
        )
    }
    return [
        {
            "distributionEventId": event.id,
            "businessName": event.title,
            "phone": event.phone,
            "deliveredAt": event.delivered_at,
            "batchId": str(event.request_id),
            "currentDisposition": states.get(event.id),
        }
        for event in events
    ]


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
    if payload.disposition in {
        "appointment_canceled",
        "appointment_no_show",
    }:
        booked = db.scalar(
            select(LeadDispositionTransition.id)
            .where(
                LeadDispositionTransition.distribution_event_id
                == event.id,
                LeadDispositionTransition.disposition
                == "appointment_booked",
            )
            .limit(1)
        )
        if booked is None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Book an appointment before reporting its later status."
                ),
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
            actor_user_id=principal.user_id,
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
        source_outcome_id=None,
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
    report, hold = apply_disposition_controls(db, event, transition)
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
        "reportId": str(report.id) if report is not None else None,
        "eligibilityHoldId": (
            str(hold.id) if hold is not None else None
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
        actor_user_id=principal.user_id,
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


def _performance_rows(
    db: Session,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    state: str = "",
    keyword: str = "",
    niche: str = "",
    confidence: str = "",
    action_state: str = "",
    latest: bool = True,
) -> list[DailySourcePerformance]:
    query = select(DailySourcePerformance)
    if start_date is not None:
        query = query.where(
            DailySourcePerformance.snapshot_date >= start_date
        )
    if end_date is not None:
        query = query.where(
            DailySourcePerformance.snapshot_date <= end_date
        )
    if state:
        query = query.where(
            DailySourcePerformance.state == state.strip().upper()
        )
    if keyword:
        query = query.where(
            DailySourcePerformance.keyword.ilike(
                f"%{keyword.strip()}%"
            )
        )
    if niche:
        query = query.where(
            DailySourcePerformance.niche == niche.strip()
        )
    if confidence:
        query = query.where(
            DailySourcePerformance.eligibility == confidence.strip()
        )
    if action_state:
        query = query.where(
            DailySourcePerformance.action_state
            == action_state.strip()
        )
    rows = list(
        db.scalars(
            query.order_by(
                DailySourcePerformance.snapshot_date.desc(),
                DailySourcePerformance.segment_key,
            )
        )
    )
    if not latest:
        return rows
    result: list[DailySourcePerformance] = []
    seen: set[str] = set()
    for row in rows:
        if row.segment_key in seen:
            continue
        seen.add(row.segment_key)
        result.append(row)
    return result


def _performance_response(item: DailySourcePerformance) -> dict:
    return {
        "id": str(item.id),
        "date": item.snapshot_date.isoformat(),
        "segment": item.segment_key,
        "state": item.state,
        "keyword": item.keyword,
        "niche": item.niche,
        "nicheConfirmed": item.niche_confirmed,
        "counts": item.counts,
        "rates": item.rates,
        "intervals": item.intervals,
        "trend": item.trend,
        "confidence": item.eligibility,
        "actionState": item.action_state,
        "evidenceChecksum": item.evidence_checksum,
    }


@app.get("/api/admin/source-performance")
def source_performance(
    start_date: date | None = None,
    end_date: date | None = None,
    state: str = "",
    keyword: str = "",
    niche: str = "",
    confidence: str = "",
    action_state: str = "",
    latest: bool = True,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    rows = _performance_rows(
        db,
        start_date=start_date,
        end_date=end_date,
        state=state,
        keyword=keyword,
        niche=niche,
        confidence=confidence,
        action_state=action_state,
        latest=latest,
    )
    from .performance import source_performance_snapshot

    return {
        **source_performance_snapshot(db),
        "rows": [_performance_response(item) for item in rows],
    }


@app.get("/api/admin/source-performance.csv")
def export_source_performance(
    start_date: date | None = None,
    end_date: date | None = None,
    state: str = "",
    keyword: str = "",
    niche: str = "",
    confidence: str = "",
    action_state: str = "",
    latest: bool = True,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    import csv
    import io

    rows = _performance_rows(
        db,
        start_date=start_date,
        end_date=end_date,
        state=state,
        keyword=keyword,
        niche=niche,
        confidence=confidence,
        action_state=action_state,
        latest=latest,
    )
    stream = io.StringIO()
    columns = [
        "date",
        "state",
        "keyword",
        "niche",
        "niche_confirmed",
        "delivered",
        "worked",
        "rated",
        "positive",
        "booked",
        "canceled",
        "no_show",
        "invalid",
        "wrong_business",
        "do_not_contact",
        "positive_rate",
        "booked_rate",
        "good_rate",
        "poor_rate",
        "confidence",
        "action_state",
    ]
    writer = csv.DictWriter(stream, fieldnames=columns)
    writer.writeheader()
    for item in rows:
        writer.writerow(
            {
                "date": item.snapshot_date.isoformat(),
                "state": item.state,
                "keyword": item.keyword,
                "niche": item.niche,
                "niche_confirmed": item.niche_confirmed,
                **item.counts,
                "positive_rate": item.rates.get(
                    "positiveResponse", 0
                ),
                "booked_rate": item.rates.get(
                    "appointmentBooked", 0
                ),
                "good_rate": item.rates.get("good", 0),
                "poor_rate": item.rates.get("poor", 0),
                "confidence": item.eligibility,
                "action_state": item.action_state,
            }
        )
    return Response(
        content=stream.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": (
                'attachment; filename="keyword-performance.csv"'
            )
        },
    )


@app.get("/api/admin/source-performance/{segment_key}/history")
def source_performance_history(
    segment_key: str,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    snapshots = list(
        db.scalars(
            select(DailySourcePerformance)
            .where(
                DailySourcePerformance.segment_key == segment_key
            )
            .order_by(DailySourcePerformance.snapshot_date.desc())
        )
    )
    notes = {
        item.snapshot_id: item
        for item in db.scalars(
            select(PerformanceSuggestionNote).where(
                PerformanceSuggestionNote.snapshot_id.in_(
                    [snapshot.id for snapshot in snapshots]
                )
            )
        )
    }
    return {
        "rows": [
            {
                **_performance_response(item),
                "suggestionNote": (
                    notes[item.id].text if item.id in notes else None
                ),
            }
            for item in snapshots
        ]
    }


@app.get("/api/admin/source-niches")
def list_source_niches(
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    from .acquisition import niche_dict

    return [
        niche_dict(item)
        for item in db.scalars(
            select(SourceNicheMapping).order_by(
                SourceNicheMapping.state,
                SourceNicheMapping.keyword,
            )
        )
    ]


@app.post("/api/admin/source-niches/{segment_key}/confirm")
def confirm_source_niche(
    segment_key: str,
    payload: SourceNicheDecision,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    mapping = db.scalar(
        select(SourceNicheMapping)
        .where(SourceNicheMapping.segment_key == segment_key)
        .with_for_update()
    )
    if mapping is None:
        raise HTTPException(
            status_code=404,
            detail="Source Segment Niche mapping was not found.",
        )
    previous = {
        "niche": mapping.niche,
        "confirmed": mapping.confirmed,
    }
    next_niche = payload.niche.strip()
    if mapping.confirmed and mapping.niche == next_niche:
        return {
            "segment": mapping.segment_key,
            "niche": mapping.niche,
            "confirmed": True,
        }
    mapping.niche = next_niche
    mapping.confirmed = True
    mapping.denied_by = ""
    mapping.denied_at = None
    mapping.confirmed_by = str(principal.user_id)
    mapping.confirmed_at = utcnow()
    mapping.updated_at = mapping.confirmed_at
    record_activity(
        db,
        action="source_niche_confirmed",
        target_type="source_segment",
        target_id=mapping.segment_key,
        actor_id=principal.user_id,
        reason=payload.reason,
        details={
            "before": previous,
            "after": {
                "niche": mapping.niche,
                "confirmed": mapping.confirmed,
            },
        },
    )
    db.commit()
    return {
        "segment": mapping.segment_key,
        "niche": mapping.niche,
        "confirmed": True,
    }


@app.post("/api/admin/source-niches/{segment_key}/deny")
def deny_source_niche(
    segment_key: str,
    payload: ActionReason,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    mapping = db.scalar(
        select(SourceNicheMapping)
        .where(SourceNicheMapping.segment_key == segment_key)
        .with_for_update()
    )
    if mapping is None:
        raise HTTPException(
            status_code=404,
            detail="Source Segment Niche mapping was not found.",
        )
    if mapping.confirmed:
        raise HTTPException(
            status_code=409,
            detail="A confirmed Source Niche mapping cannot be denied.",
        )
    if mapping.denied_at is not None:
        return {
            "segment": mapping.segment_key,
            "niche": mapping.niche,
            "confirmed": False,
            "denied": True,
        }
    mapping.denied_by = str(principal.user_id)
    mapping.denied_at = utcnow()
    mapping.updated_at = mapping.denied_at
    record_activity(
        db,
        action="source_niche_denied",
        target_type="source_segment",
        target_id=mapping.segment_key,
        actor_id=principal.user_id,
        reason=payload.reason,
        details={
            "niche": mapping.niche,
            "proposalSource": mapping.proposal_source,
            "confirmed": False,
        },
    )
    db.commit()
    return {
        "segment": mapping.segment_key,
        "niche": mapping.niche,
        "confirmed": False,
        "denied": True,
    }


@app.get("/api/admin/acquisition")
def admin_acquisition(
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """One authenticated read for the Acquisition workspace.

    Read-only by design: every action the workspace offers posts back to the
    endpoint that already owns that decision, so this contract can never
    become a second way to change acquisition.
    """
    from .acquisition import workspace as acquisition_workspace

    return acquisition_workspace(db)


@app.get("/api/admin/source-recommendations")
def list_source_recommendations(
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    from .acquisition import recommendation_dict

    return [
        recommendation_dict(item, decidable=False)
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
    payload: RecommendationDecision,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    if action not in {"approve", "deny"}:
        raise HTTPException(status_code=404, detail="Unknown action.")
    # A caller that showed the evidence decides on the evidence it showed. The
    # check happens inside decide_recommendation, under the same row lock the
    # decision takes, so it cannot be raced. A caller that showed nothing sends
    # nothing and stays unbound, which is how the legacy page still works.
    try:
        recommendation = decide_recommendation(
            db,
            recommendation_id,
            action,
            actor_id=str(principal.user_id),
            reason=payload.reason.strip(),
            apply_enabled=settings.recommendation_apply_enabled,
            shadow_mode=settings.recommendation_shadow_mode,
            expected_evidence_checksum=payload.evidence_checksum,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except RecommendationDecisionError as exc:
        db.commit()
        raise HTTPException(status_code=409, detail=str(exc)) from None
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
    try:
        billing = prepare_submission(
            db,
            customer_id=profile.customer_id,
            lead_count=payload.lead_count,
        )
    except BillingError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
        ) from None
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
    place_batch_hold(db, request=request, billing=billing)
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
    try:
        withdraw_request(db, user_id=principal.user_id, request_id=request_id)
    except RequestSubmissionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    return {"ok": True}


def _request_dict(item: LeadRequest) -> dict:
    return {
        "id": str(item.id),
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


@app.get("/api/admin/requests")
def admin_requests(_: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    return [_request_dict(item) for item in db.scalars(select(LeadRequest).order_by(LeadRequest.created_at.desc()))]


@app.get("/api/admin/fulfillment")
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


@app.get("/api/admin/requests/{request_id}")
def admin_request_detail(
    request_id: uuid.UUID,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    item = db.get(LeadRequest, request_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Request was not found.")
    return describe_request(db, item)


@app.get("/api/admin/inventory-conflicts/{conflict_id}")
def admin_inventory_conflict_detail(
    conflict_id: uuid.UUID,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    conflict = db.get(InventoryConflict, conflict_id)
    if conflict is None:
        raise HTTPException(
            status_code=404, detail="Inventory Conflict was not found."
        )
    return describe_conflict(db, conflict)


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


@app.get("/api/admin/customers/directory", response_model=CustomerDirectoryOut)
def admin_customer_directory(
    q: str = "",
    status: str = "all",
    agency_id: int | None = None,
    state: str = "",
    problems_only: bool = False,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Search Customers instead of navigating down the Agency hierarchy."""
    return build_customer_directory(
        db,
        query=q,
        status=status,
        agency_id=agency_id,
        state=state,
        problems_only=problems_only,
    )


@app.get("/api/admin/agencies/directory")
def admin_agency_directory(
    q: str = "",
    status: str = "all",
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return agency_directory(db, query=q, status=status)


@app.post("/api/admin/agencies", status_code=201)
def create_agency(
    payload: AgencyCreate,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Agency name is required.")
    reason = payload.reason.strip()
    if not reason:
        raise HTTPException(status_code=422, detail="Reason is required.")
    base = re.sub(
        r"[^a-z0-9]+",
        "-",
        (payload.slug or name).lower(),
    ).strip("-")
    base = base[:70] or "agency"
    slug = base
    suffix = 2
    while db.scalar(select(Agency.id).where(Agency.slug == slug)):
        slug = f"{base}-{suffix}"
        suffix += 1
    agency = Agency(slug=slug, name=name, active=True)
    db.add(agency)
    db.flush()
    record_activity(
        db,
        action="agency_created",
        target_type="agency",
        target_id=agency.id,
        actor_id=principal.user_id,
        reason=reason,
        details={
            "before": None,
            "after": {
                "slug": agency.slug,
                "name": agency.name,
                "active": True,
            },
        },
    )
    db.commit()
    return {
        "ok": True,
        "agencyId": agency.id,
        "slug": agency.slug,
    }


@app.get("/api/admin/agencies/{agency_id}/details")
def admin_agency_details(
    agency_id: int,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    agency = db.get(Agency, agency_id)
    if agency is None or agency.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Agency was not found.")
    return agency_details(db, agency=agency)


@app.get(
    "/api/admin/customers/{customer_id}/agency-assignment-preview"
)
def admin_customer_agency_assignment_preview(
    customer_id: int,
    agency_id: int | None = None,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    customer = db.get(Customer, customer_id)
    if customer is None or customer.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Customer was not found.")
    destination = (
        db.get(Agency, agency_id) if agency_id is not None else None
    )
    if agency_id is not None and (
        destination is None or destination.deleted_at is not None
    ):
        raise HTTPException(status_code=404, detail="Agency was not found.")
    return assignment_preview(
        db,
        customer=customer,
        destination=destination,
        settings=settings,
    )


@app.post("/api/admin/customers/{customer_id}/agency-assignment")
def admin_customer_agency_assignment(
    customer_id: int,
    payload: AgencyAssignment,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    customer = db.scalar(
        select(Customer)
        .where(Customer.id == customer_id)
        .with_for_update()
    )
    if customer is None or customer.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Customer was not found.")
    destination = (
        db.scalar(
            select(Agency)
            .where(Agency.id == payload.agency_id)
            .with_for_update()
        )
        if payload.agency_id is not None
        else None
    )
    if payload.agency_id is not None and (
        destination is None or destination.deleted_at is not None
    ):
        raise HTTPException(status_code=404, detail="Agency was not found.")
    try:
        result = assign_customer(
            db,
            customer=customer,
            destination=destination,
            actor_id=principal.user_id,
            reason=payload.reason,
            confirmed=payload.confirmed,
            settings=settings,
        )
    except AgencyAssignmentConflict as conflict:
        raise HTTPException(status_code=409, detail=str(conflict)) from None
    db.commit()
    return result


@app.get(
    "/api/admin/customers/{customer_id}/details",
    response_model=CustomerDetailsOut,
)
def admin_customer_details(
    customer_id: int,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Read one Customer, with durable identity separated from its access."""
    customer = db.get(Customer, customer_id)
    if customer is None or customer.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Customer was not found.")
    return build_customer_details(db, customer=customer)


def _allocation_customer(db: Session, customer_id: int) -> Customer:
    customer = db.get(Customer, customer_id)
    if customer is None or customer.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Customer was not found.")
    return customer


@app.get("/api/admin/customers/{customer_id}/cooldown-window")
def get_cooldown_window(
    customer_id: int,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    customer = _allocation_customer(db, customer_id)
    return {"days": customer.cooldown_window_days}


@app.put("/api/admin/customers/{customer_id}/cooldown-window")
def update_cooldown_window(
    customer_id: int,
    payload: CooldownWindowUpdate,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    customer = _allocation_customer(db, customer_id)
    customer.cooldown_window_days = payload.days
    record_activity(
        db,
        action="cooldown_window_updated",
        target_type="customer",
        target_id=customer.id,
        actor_id=principal.user_id,
        reason=payload.reason,
        details={"days": payload.days},
    )
    db.commit()
    return {"days": customer.cooldown_window_days}


@app.get("/api/admin/customers/{customer_id}/niche-policy")
def get_niche_policy(
    customer_id: int,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _allocation_customer(db, customer_id)
    return {"rows": load_policy(db, customer_id)}


@app.put("/api/admin/customers/{customer_id}/niche-policy")
def update_niche_policy(
    customer_id: int,
    payload: NichePolicyUpdate,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    customer = _allocation_customer(db, customer_id)
    try:
        rows = replace_policy(
            db, customer_id, [row.model_dump() for row in payload.rows]
        )
    except NichePolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    record_activity(
        db,
        action="niche_policy_updated",
        target_type="customer",
        target_id=customer.id,
        actor_id=principal.user_id,
        reason=payload.reason,
        details={"rows": rows},
    )
    db.commit()
    return {"rows": rows}


@app.post("/api/admin/customers/{customer_id}/niche-policy/projected-availability")
def projected_niche_policy_availability(
    customer_id: int,
    payload: NichePolicyDraft,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    customer = _allocation_customer(db, customer_id)
    draft = [row.model_dump() for row in payload.rows]
    try:
        normalize_policy_rows(draft)
    except NichePolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    from .pool_analytics import project_customer_availability

    return project_customer_availability(
        db,
        customer,
        settings,
        niche_policy_rows=draft,
    )


@app.get("/api/admin/pool-breakdown")
def get_pool_breakdown(
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    from .pool_analytics import read_pool_breakdown

    return read_pool_breakdown(db)


@app.post("/api/admin/pool-breakdown/refresh")
def refresh_pool_breakdown_endpoint(
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    from .pool_analytics import refresh_pool_breakdown

    payload = refresh_pool_breakdown(db)
    db.commit()
    return payload


@app.get("/api/admin/customers/{customer_id}/availability")
def get_customer_availability(
    customer_id: int,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _allocation_customer(db, customer_id)
    from .pool_analytics import read_customer_availability

    return read_customer_availability(db, customer_id)


@app.post("/api/admin/customers/{customer_id}/availability/refresh")
def refresh_customer_availability_endpoint(
    customer_id: int,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    customer = _allocation_customer(db, customer_id)
    from .pool_analytics import refresh_customer_availability

    payload = refresh_customer_availability(db, customer, settings)
    db.commit()
    return payload


def _store_niche_assignment_upload(
    upload: UploadFile, *, upload_id: uuid.UUID, settings: Settings
) -> Path:
    directory = Path(settings.batch_dir) / "niche-assignments"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{upload_id}.csv"
    _store_bounded_upload(upload, path)
    return path


@app.get("/api/admin/niche-assignments/export")
def export_unmapped_niche_inventory(
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    output = io.StringIO()
    output.write("phone,title,state\r\n")
    for lead in db.scalars(unmapped_inventory_query()):
        output.write(f'"{lead.phone}","{lead.title.replace(chr(34), chr(34) * 2)}","{lead.state}"\r\n')
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="unmapped-inventory.csv"'},
    )


@app.post("/api/admin/niche-assignments", status_code=202)
def upload_niche_assignments(
    file: UploadFile = File(...),
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    if not file.filename or not file.filename.casefold().endswith(".csv"):
        raise HTTPException(status_code=422, detail="Upload a CSV file.")
    item = NicheAssignmentUpload(
        uploaded_by=str(principal.user_id),
        filename=Path(file.filename).name,
        storage_path="",
    )
    db.add(item)
    db.flush()
    try:
        item.storage_path = str(
            _store_niche_assignment_upload(file, upload_id=item.id, settings=settings)
        )
    except UploadTooLargeError:
        db.rollback()
        raise HTTPException(
            status_code=413,
            detail="CSV uploads must be 10 MB or smaller.",
        ) from None
    except OSError as exc:
        db.rollback()
        raise HTTPException(
            status_code=503, detail="The Niche Assignment CSV could not be stored."
        ) from exc
    enqueue_job(
        db, "ingest_niche_assignments", payload={"niche_assignment_upload_id": str(item.id)}
    )
    record_activity(
        db,
        action="niche_assignment_uploaded",
        target_type="niche_assignment_upload",
        target_id=item.id,
        actor_id=principal.user_id,
        reason="Administrator uploaded Niche Assignments",
        details={"filename": item.filename},
    )
    db.commit()
    return niche_assignment_upload_status(item)


@app.get("/api/admin/niche-assignments/{upload_id}")
def niche_assignment_status(
    upload_id: uuid.UUID,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    item = db.get(NicheAssignmentUpload, upload_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Niche Assignment upload was not found.")
    return niche_assignment_upload_status(item)


@app.get("/api/admin/customers/{customer_id}/billing")
def admin_customer_billing(
    customer_id: int,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    customer = db.get(Customer, customer_id)
    if customer is None or customer.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Customer was not found.")
    result = wallet_view(db, customer)
    record_activity(
        db,
        action="credit_wallet_viewed",
        target_type="customer",
        target_id=customer.id,
        actor_id=principal.user_id,
        reason="Administrator viewed the Credit Wallet and Credit Ledger.",
        details={
            "balanceCents": result["balanceCents"],
            "ledgerEntries": len(result["ledger"]),
        },
    )
    db.commit()
    return result


@app.put("/api/admin/customers/{customer_id}/billing")
@app.patch(
    "/api/admin/customers/{customer_id}/billing",
    include_in_schema=False,
)
def update_customer_billing(
    customer_id: int,
    payload: CustomerBillingUpdate,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    try:
        customer = configure_customer_billing(
            db,
            customer_id=customer_id,
            billing_enabled=payload.billing_enabled,
            lead_rate_cents_per_thousand=(
                payload.lead_rate_cents_per_thousand
            ),
            actor_id=principal.user_id,
            reason=payload.reason,
        )
    except BillingError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
        ) from None
    db.commit()
    return wallet_view(db, customer)


@app.post(
    "/api/admin/customers/{customer_id}/billing/adjustments",
    status_code=201,
)
def create_credit_adjustment(
    customer_id: int,
    payload: CreditAdjustmentCreate,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    try:
        post_admin_adjustment(
            db,
            customer_id=customer_id,
            amount_cents=payload.amount_cents,
            actor_id=principal.user_id,
            reason=payload.reason,
        )
    except BillingError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
        ) from None
    customer = db.get(Customer, customer_id)
    db.commit()
    return wallet_view(db, customer)


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


@app.get("/api/admin/scrape-runs/{run_id}")
def get_scrape_run(
    run_id: int,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    run = db.get(ScraperRun, run_id)
    if run is None:
        raise HTTPException(
            status_code=404,
            detail="Scrape Run was not found.",
        )
    return {
        "id": run.id,
        "source": run.source,
        "sourceVersion": run.source_version,
        "configurationId": (
            str(run.configuration_id)
            if run.configuration_id is not None
            else None
        ),
        "datasetVersion": run.dataset_version,
        "manual": run.manual,
        "checksum": run.checksum,
        "status": run.status,
        "rowsSeen": run.rows_seen,
        "rowsImported": run.rows_imported,
        "details": run.details,
        "startedAt": run.started_at,
        "finishedAt": run.finished_at,
        "segments": [
            {
                "key": result.segment_key,
                "niche": result.niche,
                "geography": result.geography,
                "observed": result.observed_count,
                "valid": result.valid_count,
                "new": result.new_count,
                "duplicates": result.duplicate_count,
                "quarantined": result.quarantined_count,
                "anomalous": result.anomalous,
                "anomalyReasons": result.anomaly_reasons,
            }
            for result in db.scalars(
                select(ScrapeSegmentResult)
                .where(ScrapeSegmentResult.scraper_run_id == run.id)
                .order_by(ScrapeSegmentResult.segment_key)
            )
        ],
    }


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
    db.flush()
    record_activity(
        db,
        action="scraper_configuration_created",
        target_type="scraper_configuration",
        target_id=item.id,
        actor_id=principal.user_id,
        reason=payload.reason,
        details={
            "before": None,
            "after": {
                "version": item.version,
                "status": item.status,
                "segmentCount": len(segments),
            },
            "checksum": item.checksum,
        },
    )
    db.commit()
    db.refresh(item)
    return _configuration_dict(item)


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
    previous_status = item.status
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
    record_activity(
        db,
        action="scraper_configuration_scheduled",
        target_type="scraper_configuration",
        target_id=item.id,
        actor_id=principal.user_id,
        reason=payload.reason,
        details={
            "before": {"status": previous_status},
            "after": {"status": "scheduled"},
            "version": item.version,
        },
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
    record_activity(
        db,
        action="scraper_manual_run_queued",
        target_type="scraper_configuration",
        target_id=item.id,
        actor_id=principal.user_id,
        reason=payload.reason,
        details={
            "before": {"manualRunQueued": False},
            "after": {"manualRunQueued": True},
            "version": item.version,
        },
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
    record_activity(
        db,
        action="scraper_configuration_rollback_scheduled",
        target_type="scraper_configuration",
        target_id=item.id,
        actor_id=principal.user_id,
        reason=payload.reason,
        details={
            "before": None,
            "after": {"status": "scheduled"},
            "version": item.version,
            "basedOnConfigurationId": str(source.id),
        },
    )
    db.commit()
    db.refresh(item)
    return _configuration_dict(item)


def _evidence_payload(correction: LeadCorrectionEvent) -> dict:
    """What the override disagreed with, as the correction row recorded it."""
    return {
        "kind": correction.based_on_kind,
        "title": correction.based_on_title,
        "state": correction.based_on_state,
        "observationId": correction.based_on_observation_id,
    }


def _correction_activity(
    db: Session,
    correction: LeadCorrectionEvent,
    *,
    principal: Principal,
    reason: str,
    origin: dict | None = None,
) -> None:
    record_activity(
        db,
        action="lead_correction_applied",
        target_type="lead",
        target_id=correction.lead_id,
        actor_id=principal.user_id,
        reason=reason,
        details={
            "before": {
                "correctionId": (
                    str(correction.supersedes_correction_id)
                    if correction.supersedes_correction_id
                    else None
                ),
                "title": correction.based_on_title,
                "state": correction.based_on_state,
            },
            "after": {
                "correctionId": str(correction.id),
                "title": correction.title,
                "state": correction.state,
            },
            # The evidence travels with the decision, so a later Scrape Run
            # superseding the Current Listing cannot strand this override
            # with nothing to judge it against.
            "evidence": _evidence_payload(correction),
            **(origin or {}),
        },
    )


@app.put("/api/admin/leads/{lead_id}/suppression")
def suppress_lead(
    lead_id: int,
    payload: ActionReason,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Make a Lead ineligible for every Customer, with a recorded reason."""
    lead = db.scalar(
        select(Lead).where(Lead.id == lead_id).with_for_update()
    )
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead was not found.")
    if not lead.suppressed:
        lead.suppressed = True
        lead.suppression_reason = payload.reason.strip()
        record_activity(
            db,
            action="lead_suppressed",
            target_type="lead",
            target_id=lead.id,
            actor_id=principal.user_id,
            reason=payload.reason,
            details={
                "before": {"suppressed": False},
                "after": {"suppressed": True},
            },
        )
    db.commit()
    return {
        "leadId": lead.id,
        "suppressed": lead.suppressed,
        "reason": lead.suppression_reason,
        "restoreNotice": RESTORE_NOTICE,
    }


@app.delete("/api/admin/leads/{lead_id}/suppression")
def unsuppress_lead(
    lead_id: int,
    payload: ActionReason,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Return a Lead to the ordinary allocation rules, with a recorded reason.

    Restoring eligibility is not an allocation: Global Cooldown, permanent
    no-repeat history, and Licensed State scope all still apply afterwards.
    ``restoreNotice`` says so wherever this is surfaced.
    """
    lead = db.scalar(
        select(Lead).where(Lead.id == lead_id).with_for_update()
    )
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead was not found.")
    if lead.suppressed:
        previous_reason = lead.suppression_reason
        lead.suppressed = False
        lead.suppression_reason = ""
        record_activity(
            db,
            action="lead_unsuppressed",
            target_type="lead",
            target_id=lead.id,
            actor_id=principal.user_id,
            reason=payload.reason,
            details={
                "before": {
                    "suppressed": True,
                    "suppressionReason": previous_reason,
                },
                "after": {"suppressed": False},
            },
        )
    db.commit()
    return {
        "leadId": lead.id,
        "suppressed": lead.suppressed,
        "reason": lead.suppression_reason,
        "restoreNotice": RESTORE_NOTICE,
    }


@app.put("/api/admin/leads/{lead_id}/correction")
def apply_lead_correction(
    lead_id: int,
    payload: LeadCorrectionApply,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Override a Lead's delivered title or state against its evidence."""
    lead = db.scalar(
        select(Lead).where(Lead.id == lead_id).with_for_update()
    )
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead was not found.")
    try:
        correction = record_correction(
            db,
            lead,
            actor_id=principal.user_id,
            reason=payload.reason,
            title=payload.title,
            state=payload.state,
        )
    except ControlConflict as conflict:
        db.rollback()
        raise HTTPException(status_code=422, detail=conflict.message) from None
    _correction_activity(
        db,
        correction,
        principal=principal,
        reason=payload.reason,
    )
    db.commit()
    return {
        "leadId": lead.id,
        "correctionId": str(correction.id),
        "title": lead.title,
        "state": lead.state,
        "evidence": _evidence_payload(correction),
    }


@app.delete("/api/admin/leads/{lead_id}/correction")
def remove_lead_correction(
    lead_id: int,
    payload: ActionReason,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Drop the override so the Lead falls back to the evidence underneath."""
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
    previous = {
        "correctionId": str(active.id),
        "title": lead.title,
        "state": lead.state,
    }
    # Resolve the fallback *after* clearing the override, so the evidence
    # reported is what the Lead actually reverts to rather than the override
    # that is going away. Restore the override if there is nowhere to land.
    lead.active_correction_id = None
    evidence = lead_evidence(db, lead)
    if evidence.kind == "none":
        lead.active_correction_id = active.id
        raise HTTPException(
            status_code=409,
            detail=(
                "This Lead Correction cannot be removed because there is no "
                "Current Listing or Legacy Listing Snapshot to fall back to."
            ),
        )
    removal = LeadCorrectionEvent(
        lead_id=lead.id,
        action="removed",
        title=active.title,
        state=active.state,
        actor_id=str(principal.user_id),
        reason=payload.reason.strip(),
        supersedes_correction_id=active.id,
        based_on_kind=evidence.kind,
        based_on_observation_id=evidence.observation_id,
        based_on_title=evidence.title,
        based_on_state=evidence.state,
    )
    db.add(removal)
    lead.title = evidence.title
    lead.state = evidence.state
    record_activity(
        db,
        action="lead_correction_removed",
        target_type="lead",
        target_id=lead.id,
        actor_id=principal.user_id,
        reason=payload.reason,
        details={
            "before": previous,
            "after": {
                "correctionId": None,
                "title": lead.title,
                "state": lead.state,
            },
            "evidence": _evidence_payload(removal),
        },
    )
    db.commit()
    return {
        "leadId": lead.id,
        "correctionId": None,
        "title": lead.title,
        "state": lead.state,
        "evidence": _evidence_payload(removal),
    }


@app.get("/api/admin/lead-reports/{report_id}")
def admin_lead_report_detail(
    report_id: uuid.UUID,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """One Lead Report, its evidence, and the controls sitting beside it."""
    report = db.get(LeadReport, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Lead Report was not found.")
    return describe_report(db, report)


def _open_report(db: Session, report_id: uuid.UUID) -> LeadReport:
    report = db.scalar(
        select(LeadReport)
        .where(LeadReport.id == report_id)
        .with_for_update()
    )
    if report is None:
        raise HTTPException(status_code=404, detail="Lead Report was not found.")
    return report


def _report_activity(
    db: Session,
    report: LeadReport,
    *,
    action: str,
    principal: Principal,
    note: str,
    after: dict,
) -> None:
    record_activity(
        db,
        action=f"lead_report_{action}",
        target_type="lead_report",
        target_id=report.id,
        actor_id=principal.user_id,
        reason=note,
        details={
            "before": {"status": "open"},
            "after": {"status": report.status, **after},
            "distributionEventId": report.distribution_event_id,
            "reportReason": report.reason,
        },
    )


# The three resolutions are separate endpoints because they are separate
# decisions. A single /resolve taking an action name made "dismiss" and
# "suppress" look like variations of one act, and let a caller send a title to
# an endpoint that has nowhere to put one.


@app.post("/api/admin/lead-reports/{report_id}/dismiss")
def dismiss_lead_report(
    report_id: uuid.UUID,
    payload: LeadReportNote,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Judge the report unfounded. Releases the hold; the Lead is untouched."""
    report = _open_report(db, report_id)
    try:
        dismiss_report(
            db,
            report,
            actor_id=principal.user_id,
            note=payload.note,
        )
    except ControlConflict as conflict:
        db.rollback()
        raise HTTPException(status_code=409, detail=conflict.message) from None
    _report_activity(
        db,
        report,
        action="dismissed",
        principal=principal,
        note=payload.note,
        after={"eligibilityHeld": False, "leadChanged": False},
    )
    db.commit()
    return {
        "reportId": str(report.id),
        "status": report.status,
        "eligibilityHeld": False,
    }


@app.post("/api/admin/lead-reports/{report_id}/correct")
def correct_lead_report(
    report_id: uuid.UUID,
    payload: LeadReportCorrect,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Override the Lead's delivered values in answer to the report."""
    report = _open_report(db, report_id)
    try:
        correction = correct_from_report(
            db,
            report,
            actor_id=principal.user_id,
            note=payload.note,
            title=payload.title,
            state=payload.state,
        )
    except ControlConflict as conflict:
        db.rollback()
        raise HTTPException(status_code=422, detail=conflict.message) from None
    _report_activity(
        db,
        report,
        action="corrected",
        principal=principal,
        note=payload.note,
        after={
            "eligibilityHeld": False,
            "correctionId": str(correction.id),
        },
    )
    # Correcting a Lead is consequential in its own right, so it is recorded
    # on the Lead as well. Reading only the report's Activity would otherwise
    # leave the Lead's own history with an unexplained change of values.
    _correction_activity(
        db,
        correction,
        principal=principal,
        reason=payload.note,
        origin={"leadReportId": str(report.id)},
    )
    db.commit()
    return {
        "reportId": str(report.id),
        "status": report.status,
        "correctionId": str(correction.id),
        "title": correction.title,
        "state": correction.state,
        "evidence": _evidence_payload(correction),
    }


@app.post("/api/admin/lead-reports/{report_id}/suppress")
def suppress_lead_report(
    report_id: uuid.UUID,
    payload: LeadReportNote,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Convert the report into audited Lead Suppression."""
    report = _open_report(db, report_id)
    try:
        lead = suppress_from_report(
            db,
            report,
            actor_id=principal.user_id,
            note=payload.note,
        )
    except ControlConflict as conflict:
        db.rollback()
        raise HTTPException(status_code=409, detail=conflict.message) from None
    _report_activity(
        db,
        report,
        action="suppressed",
        principal=principal,
        note=payload.note,
        after={"eligibilityHeld": False, "leadSuppressed": True},
    )
    # Suppression is an eligibility change, so it is recorded against the Lead
    # under the same action name a direct suppression uses.
    record_activity(
        db,
        action="lead_suppressed",
        target_type="lead",
        target_id=lead.id,
        actor_id=principal.user_id,
        reason=payload.note,
        details={
            "before": {"suppressed": False},
            "after": {"suppressed": True},
            "leadReportId": str(report.id),
        },
    )
    db.commit()
    return {
        "reportId": str(report.id),
        "status": report.status,
        "leadId": lead.id,
        "suppressed": True,
        "restoreNotice": RESTORE_NOTICE,
    }


@app.put("/api/admin/customers/{customer_id}/user-account")
def replace_user_account(
    customer_id: int,
    payload: UserAccountReplace,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Provision a known authentication identity as the Customer's access.

    First provisioning takes effect immediately. Anything that would displace
    an existing active User Account is recorded as a pending invitation
    instead, so the Customer keeps working until the replacement is accepted.
    """
    customer = _locked_customer(db, customer_id)
    try:
        result = invite_user_account(
            db,
            customer=customer,
            auth_user_id=payload.auth_user_id,
            email=str(payload.email),
            actor_id=principal.user_id,
            reason=payload.reason,
        )
    except UserAccountConflict as conflict:
        raise HTTPException(
            status_code=409,
            detail=conflict.message,
        ) from None
    db.commit()
    return _provision_response(customer, result)


def _locked_customer(db: Session, customer_id: int) -> Customer:
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
    return customer


def _provision_response(customer: Customer, result: ProvisionResult) -> dict:
    return {
        "customerId": customer.id,
        "authUserId": str(result.auth_user_id),
        "email": result.email,
        "licensedStates": normalize_states(customer.licensed_states),
        "activated": result.activated,
        "invitationId": (
            str(result.invitation_id) if result.invitation_id else None
        ),
        "replacesAuthUserId": (
            str(result.replaces_auth_user_id)
            if result.replaces_auth_user_id
            else None
        ),
    }


@app.post(
    "/api/admin/customers/{customer_id}/user-account-invitation",
    status_code=201,
)
async def invite_customer_user_account(
    customer_id: int,
    payload: UserAccountInvite,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Invite a replacement User Account without disturbing current access.

    The provider is asked first. If the invitation cannot be dispatched
    nothing is written, so a failed send leaves the Customer exactly as it
    was rather than stranding a half-made replacement.
    """
    customer = _locked_customer(db, customer_id)
    if not customer.active:
        raise HTTPException(
            status_code=409,
            detail=(
                "A User Account cannot be provisioned for a deactivated "
                "Customer."
            ),
        )
    if pending_invitation(db, customer.id) is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                "This Customer already has an outstanding User Account "
                "invitation. Cancel it before inviting a different account."
            ),
        )
    auth_user_id = await _dispatch_invitation(settings, payload)
    try:
        result = invite_user_account(
            db,
            customer=customer,
            auth_user_id=auth_user_id,
            email=str(payload.email),
            actor_id=principal.user_id,
            reason=payload.reason,
        )
    except UserAccountConflict as conflict:
        raise HTTPException(
            status_code=409,
            detail=conflict.message,
        ) from None
    db.commit()
    return _provision_response(customer, result)


@app.delete("/api/admin/customers/{customer_id}/user-account-invitation")
def cancel_customer_user_account_invitation(
    customer_id: int,
    payload: ActionReason,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Withdraw an outstanding invitation. Current access is untouched."""
    customer = _locked_customer(db, customer_id)
    invitation = cancel_user_account_invitation(
        db,
        customer=customer,
        actor_id=principal.user_id,
        reason=payload.reason,
    )
    if invitation is None:
        raise HTTPException(
            status_code=404,
            detail="This Customer has no outstanding invitation.",
        )
    db.commit()
    return {"ok": True, "invitationId": str(invitation.id)}


@app.post("/api/admin/user-accounts/sync")
@app.post("/api/admin/recipients/sync", include_in_schema=False)
async def sync_user_accounts(
    principal: Principal = Depends(require_admin),
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
    seen = created = updated = proposed = 0
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
                if profile.email != email:
                    updated += 1
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
    if created or updated or proposed:
        record_activity(
            db,
            action="user_accounts_synchronized",
            target_type="user_account",
            target_id="bulk-sync",
            actor_id=principal.user_id,
            reason="Synchronized User Accounts from the identity provider.",
            details={
                "before": None,
                "after": {
                    "created": created,
                    "updated": updated,
                    "proposedMappings": proposed,
                },
                "seen": seen,
            },
        )
    db.commit()
    return {"seen": seen, "created": created, "proposedMappings": proposed, "allMappingsRequireConfirmation": True}


@app.patch("/api/admin/user-accounts/{user_id}/customer")
@app.patch("/api/admin/recipients/{user_id}", include_in_schema=False)
def map_user_account_customer(
    user_id: uuid.UUID,
    payload: CustomerMappingUpdate,
    principal: Principal = Depends(require_admin),
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
    previous = {
        "customerId": profile.customer_id,
        "confirmed": profile.mapping_confirmed_at is not None,
    }
    after = {
        "customerId": customer.id,
        "confirmed": payload.confirmed,
    }
    if previous == after:
        return {"ok": True}
    profile.customer_id = customer.id
    profile.mapping_confirmed_at = (
        datetime.now(timezone.utc) if payload.confirmed else None
    )
    record_activity(
        db,
        action="user_account_customer_mapped",
        target_type="customer",
        target_id=customer.id,
        actor_id=principal.user_id,
        reason="Confirmed User Account to Customer mapping.",
        details={
            "before": previous,
            "after": after,
            "authUserId": str(profile.user_id),
        },
    )
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
    reason = payload.reason.strip()
    if not reason:
        raise HTTPException(status_code=422, detail="Reason is required.")
    previous = {
        "name": agency.name,
        "active": agency.active,
    }
    agency.name = name
    agency.active = payload.active
    after = {
        "name": agency.name,
        "active": agency.active,
    }
    if previous != after:
        record_activity(
            db,
            action="agency_updated",
            target_type="agency",
            target_id=agency.id,
            actor_id=principal.user_id,
            reason=reason,
            details={
                "before": previous,
                "after": after,
            },
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
    if payload.agency_id != customer.agency_id:
        raise HTTPException(
            status_code=409,
            detail=(
                "Agency membership changes require a consequence preview "
                "and explicit confirmation."
            ),
        )
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Customer name is required.")
    previous = {
        "name": customer.name,
        "active": customer.active,
        "agencyId": customer.agency_id,
    }
    customer.name = name
    customer.active = payload.active
    after = {
        "name": customer.name,
        "active": customer.active,
        "agencyId": customer.agency_id,
    }
    if previous != after:
        record_activity(
            db,
            action="customer_updated",
            target_type="customer",
            target_id=customer.id,
            actor_id=principal.user_id,
            reason=payload.reason,
            details={
                "before": previous,
                "after": after,
            },
        )
    db.commit()
    return {"ok": True}


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
    dependencies = customer_dependency_counts(db, customer.id)
    if payload.hard_delete:
        if any(dependencies.values()):
            refused_customer_id = customer.id
            db.rollback()
            record_activity(
                db,
                action="customer_hard_delete_refused",
                target_type="customer",
                target_id=refused_customer_id,
                actor_id=principal.user_id,
                reason=payload.reason,
                details={
                    "before": {"deleted": False},
                    "after": {"deleted": False},
                    "guard": "dependent_history",
                    "dependencies": dependencies,
                },
            )
            db.commit()
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
        record_activity(
            db,
            action="customer_hard_deleted",
            target_type="customer",
            target_id=customer.id,
            actor_id=principal.user_id,
            reason=payload.reason,
            details={
                "before": {"deleted": False},
                "after": {"deleted": True},
            },
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
    # An outstanding invitation would otherwise still be acceptable and would
    # hand a removed Customer working access.
    cancel_user_account_invitation(
        db,
        customer=customer,
        actor_id=principal.user_id,
        reason=payload.reason,
    )
    customer.deleted_at = utcnow()
    record_activity(
        db,
        action="customer_deleted",
        target_type="customer",
        target_id=customer.id,
        actor_id=principal.user_id,
        reason=payload.reason,
        details={
            "before": {"deleted": False},
            "after": {
                "deleted": True,
                "historyPreserved": True,
            },
            "dependencies": dependencies,
        },
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
    for invitation in db.scalars(
        select(UserAccountInvitation).where(
            UserAccountInvitation.customer_id == customer.id
        )
    ):
        invitation.email = anonymous_email
        if invitation.status == "pending":
            invitation.status = "canceled"
            invitation.canceled_at = utcnow()
    original_slug = customer.slug
    customer.name = "Deleted Customer"
    customer.slug = f"deleted-customer-{customer.id}"
    customer.licensed_states = []
    customer.deleted_at = customer.deleted_at or utcnow()
    record_activity(
        db,
        action="customer_personal_data_erased",
        target_type="customer",
        target_id=customer.id,
        actor_id=principal.user_id,
        reason=payload.reason,
        details={
            "before": {"personalDataErased": False},
            "after": {
                "personalDataErased": True,
                "tombstoneId": str(tombstone.id),
            },
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
    principal: Principal = Depends(require_admin),
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
    record_activity(
        db,
        action="customer_deleted",
        target_type="customer",
        target_id=agent.id,
        actor_id=principal.user_id,
        reason="Legacy Customer deletion endpoint.",
        details={
            "before": {"deleted": False},
            "after": {
                "deleted": True,
                "historyPreserved": True,
            },
            "unassignedUserAccounts": len(profiles),
        },
    )
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
            "membershipHistory": int(
                db.scalar(
                    select(func.count(AgencyMembershipHistory.id)).where(
                        AgencyMembershipHistory.agency_id == agency.id
                    )
                )
                or 0
            ),
        }
        if any(dependencies.values()):
            refused_agency_id = agency.id
            db.rollback()
            record_activity(
                db,
                action="agency_hard_delete_refused",
                target_type="agency",
                target_id=refused_agency_id,
                actor_id=principal.user_id,
                reason=payload.reason,
                details={
                    "before": {"deleted": False},
                    "after": {"deleted": False},
                    "guard": "dependent_history",
                    "dependencies": dependencies,
                },
            )
            db.commit()
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
        record_activity(
            db,
            action="agency_hard_deleted",
            target_type="agency",
            target_id=agency.id,
            actor_id=principal.user_id,
            reason=payload.reason,
            details={
                "before": {"deleted": False},
                "after": {"deleted": True},
            },
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
    record_activity(
        db,
        action="agency_deleted",
        target_type="agency",
        target_id=agency.id,
        actor_id=principal.user_id,
        reason=payload.reason,
        details={
            "before": {"deleted": False},
            "after": {
                "deleted": True,
                "historyPreserved": True,
            },
            "deletedCustomers": deleted_customers,
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


def _customer_invitation_redirect(settings: Settings) -> str:
    return f"{settings.public_base_url.rstrip('/')}/app/accept-invitation"


async def _dispatch_invitation(settings: Settings, payload) -> uuid.UUID:
    """Ask the provider to invite an email and return its identity.

    Jawnix never sets or reads a credential. The provider owns the invitation
    link and the password behind it; all that comes back here is the
    authentication identity to attach to a Customer.
    """
    redirect_to = _customer_invitation_redirect(settings)
    created = await _supabase_admin(
        settings,
        "POST",
        f"/auth/v1/invite?{urlencode({'redirect_to': redirect_to})}",
        {
            "email": str(payload.email).lower(),
            "data": {
                "first_name": payload.first_name,
                "last_name": payload.last_name,
            },
        },
    )
    return uuid.UUID(str(created["id"]))


async def _send_password_reset(settings: Settings, email: str) -> None:
    if not settings.supabase_url or not settings.supabase_anon_key:
        raise HTTPException(status_code=503, detail="Supabase password recovery is not configured.")
    redirect_to = _customer_invitation_redirect(settings)
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
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    profile = db.get(CustomerProfile, user_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Recipient was not found.")
    await _send_password_reset(settings, profile.email)
    record_activity(
        db,
        action="user_account_password_reset_sent",
        target_type="user_account",
        target_id=user_id,
        actor_id=principal.user_id,
        reason="Administrator sent a User Account password reset.",
        details={
            "before": {"resetDispatched": False},
            "after": {"resetDispatched": True},
            "customerId": profile.customer_id,
        },
    )
    db.commit()
    return {"ok": True, "email": profile.email}


def _customer_slug(db: Session, name: str, requested: str | None) -> str:
    """Derive a stable, unique slug for a brand-new Customer."""
    base = re.sub(r"[^a-z0-9]+", "-", (requested or name).lower()).strip("-")
    base = base[:70] or "customer"
    candidate = base
    suffix = 2
    while db.scalar(select(Customer.id).where(Customer.slug == candidate)):
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


@app.post("/api/admin/customers", status_code=201)
async def create_customer(
    payload: CustomerCreate,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Create a truly new, independent Customer and invite its access.

    The durable Customer and its first User Account are created together, but
    they stay separate things: the Customer owns the Licensed States, Agency
    membership, and all future history, while the invited account is only the
    way somebody signs in on its behalf.

    The provider invitation is dispatched before anything commits, so a failed
    send leaves no half-created Customer behind.
    """
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Customer name is required.")
    if payload.agency_id is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                "Create the Customer first, then preview and confirm its "
                "Agency assignment."
            ),
        )
    slug = _customer_slug(db, name, payload.slug)
    auth_user_id = await _dispatch_invitation(settings, payload)
    creation_reason = "Created a Customer and invited its User Account"

    customer = Customer(
        slug=slug,
        name=name,
        licensed_states=[],
        agency_id=None,
        active=True,
    )
    db.add(customer)
    db.flush()
    profile = db.get(CustomerProfile, auth_user_id)
    if profile is None:
        profile = CustomerProfile(
            user_id=auth_user_id,
            email=str(payload.email).lower(),
            first_name=payload.first_name.strip(),
            last_name=payload.last_name.strip(),
            licensed_states=[],
        )
        db.add(profile)
    else:
        profile.first_name = payload.first_name.strip() or profile.first_name
        profile.last_name = payload.last_name.strip() or profile.last_name
    db.flush()
    record_activity(
        db,
        action="customer_created",
        target_type="customer",
        target_id=customer.id,
        actor_id=principal.user_id,
        reason=creation_reason,
        details={
            "before": None,
            "after": {
                "slug": customer.slug,
                "name": customer.name,
                "active": True,
                "agencyId": customer.agency_id,
                "licensedStates": list(customer.licensed_states),
            },
        },
    )
    try:
        result = invite_user_account(
            db,
            customer=customer,
            auth_user_id=auth_user_id,
            email=str(payload.email),
            actor_id=principal.user_id,
            reason=creation_reason,
        )
    except UserAccountConflict as conflict:
        raise HTTPException(
            status_code=409,
            detail=conflict.message,
        ) from None
    db.commit()
    return {
        "ok": True,
        "customerId": customer.id,
        "slug": customer.slug,
        "userId": str(result.auth_user_id),
        "mappingConfirmed": result.activated,
    }


@app.post("/api/admin/requests/{request_id}/{action}")
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
    # Shares the offer surface's predicate: if these two ever disagreed the
    # workspace would offer a regeneration this endpoint then refuses.
    if artifact_available(artifact, now=now):
        raise HTTPException(
            status_code=409,
            detail="Batch Artifact has not expired.",
        )
    previous_artifact = (
        {
            "artifactId": artifact.id,
            "available": False,
            "expiresAt": (
                artifact.expires_at.isoformat()
                if artifact.expires_at is not None
                else None
            ),
        }
        if artifact is not None
        else None
    )
    artifact = generate_artifact(db, item, events, settings)
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


@app.post("/api/admin/scrape-anomalies/{anomaly_id}/{action}")
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
                item.closed_at = utcnow()
                enqueue_milestone_email(db, item)
                enqueue_job(db, "update_notification", item.id)
    db.commit()
    return {"ok": True}
