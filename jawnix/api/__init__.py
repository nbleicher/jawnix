from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from urllib.parse import urlencode
import httpx
from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Request,
    Response,
)
from fastapi.routing import APIRoute, request_response
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..activity import record_activity
from ..auth import (
    RETIRED_MANAGER_MESSAGE,
    RETIRED_MANAGER_ROLE,
    Principal,
    issue_session,
    require_admin,
    require_principal,
    verify_supabase_token,
)
from ..admin_mfa_api import router as admin_mfa_router
from ..billing import initialize_customer_billing
from ..config import Settings, get_settings
from ..customer_accounts import (
    UserAccountConflict,
    accept_user_account_invitation,
    invite_user_account,
)
from ..customer_history import archived_customer_matches, request_history_scope
from ..customer_registration import (
    CUSTOMER_HOME_PATH,
    complete_registration,
    lock_registration_identity,
    registration_next,
)
from ..database import get_db
from ..frontend import register_frontend_shell
from ..models import (
    AdminMFAState,
    Customer,
    CustomerProfile,
    CustomerTombstone,
    LeadRequest,
    ScraperConfiguration,
    SourceSegment,
    UserAccount,
    CustomerRegistration,
)
from ..mfa_provider import MFAProviderError, get_mfa_provider
from ..schemas import CustomerCreate, SessionExchange, ScraperConfigurationCreate
from ..scraper_proxy import native_router as native_scraper_router
from ..request_updates import request_events, request_updates_lifespan
from ..lead_upload_api import router as lead_upload_router
from ..acquisition_yield_api import router as acquisition_yield_router
from ._shared import _current_customer_profile
from .scraper_config import _configuration_dict
from ._shared import MAX_CSV_UPLOAD_BYTES as MAX_CSV_UPLOAD_BYTES


app = FastAPI(title="Jawnix VPS API", version="1.0.0", lifespan=request_updates_lifespan)


app.include_router(admin_mfa_router)


app.include_router(acquisition_yield_router)


app.include_router(native_scraper_router)


app.include_router(lead_upload_router)


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


@app.post("/api/auth/session")
async def create_session(
    payload: SessionExchange,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    user = await verify_supabase_token(payload.access_token, settings)
    role = str((user.get("app_metadata") or {}).get("jawnix_role") or "customer")
    if role == RETIRED_MANAGER_ROLE:
        raise HTTPException(status_code=403, detail=RETIRED_MANAGER_MESSAGE)
    if role not in {"admin", "customer"}:
        raise HTTPException(status_code=403, detail="This identity has no Jawnix access.")
    if payload.requested_next == "/admin.html" and role != "admin":
        raise HTTPException(status_code=403, detail="Sign in with noah@jawnix.com to access administration.")
    session_kwargs: dict = {}
    admin_next = "/app/admin/today"
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
        if len(verified) < 1:
            admin_next = "/app/admin/mfa/enroll"
        elif assurance != "aal2":
            admin_next = "/app/admin/mfa/challenge"
        else:
            requested = payload.requested_next or ""
            admin_next = (
                requested
                if requested.startswith("/app/admin/")
                and not requested.startswith("//")
                else admin_next
            )
    principal = issue_session(
        response,
        user,
        settings,
        **session_kwargs,
    )
    if principal.role == "customer":
        lock_registration_identity(db, principal.user_id)
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
    if profile is None and principal.role == "customer":
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
        if profile.customer_id is not None and principal.role == "customer":
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
    signup_profile = (user.get("user_metadata") or {}).get("signup_profile")
    registration = db.get(CustomerRegistration, principal.user_id)
    if principal.role == "customer" and signup_profile is not None and account is None:
        if user.get("email_verified") is not True:
            raise HTTPException(status_code=403, detail="Verify your email before completing signup.")
        from ..schemas import SignupProfile
        try:
            details = SignupProfile.model_validate(signup_profile)
        except ValueError:
            raise HTTPException(status_code=422, detail="The signup details are invalid.") from None
        db.flush()
        registration = complete_registration(db, user_id=principal.user_id, email=principal.email, details=details, settings=settings)
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
            else CUSTOMER_HOME_PATH
        )
        if registration is not None and registration_next(registration) != CUSTOMER_HOME_PATH:
            next_path = registration_next(registration)
    return {
        "ok": True,
        "role": principal.role,
        "assurance": principal.assurance,
        "next": next_path,
    }


@app.get("/api/me/batch-requests/events")
def customer_batch_request_events(
    request: Request,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    profile = _current_customer_profile(db, principal)
    scope = {str(value) for value in db.scalars(select(LeadRequest.id).where(request_history_scope(profile, principal.user_id)))}
    db.close()
    async def authorize():
        try:
            _current_customer_profile(db, require_principal(request, settings))
        finally:
            db.close()
    return StreamingResponse(request_events(request, scope, authorize), media_type="text/event-stream", headers={"Cache-Control":"private, no-store", "X-Accel-Buffering":"no"})


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


async def _supabase_admin(settings: Settings, method: str, path: str, payload: dict | None = None):
    if settings.auth_provider == "better-auth":
        provider = get_mfa_provider(settings)
        try:
            page = int(path.split("page=", 1)[1].split("&", 1)[0])
            return await provider.request("users", page=page)
        except (MFAProviderError, ValueError, IndexError):
            raise HTTPException(status_code=502, detail="Identity administration failed.") from None
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
    if settings.auth_provider == "better-auth":
        provider = get_mfa_provider(settings)
        try:
            email = str(payload.email).lower()
            created = await provider.request("provision-user", email=email, name=f"{payload.first_name} {payload.last_name}".strip())
            await provider.request("reset-user", email=email)
            return uuid.UUID(created["id"])
        except MFAProviderError:
            raise HTTPException(status_code=502, detail="The invitation could not be sent.") from None
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
    if settings.auth_provider == "better-auth":
        try:
            # "reset" lands on the password reset page rather than the
            # invitation page; an older identity service ignores it.
            await get_mfa_provider(settings).request(
                "reset-user", email=email, purpose="reset"
            )
            return
        except MFAProviderError:
            raise HTTPException(status_code=502, detail="Password recovery could not be sent.") from None
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
    matches = archived_customer_matches(db, payload.first_name, payload.last_name)
    customer = None
    if payload.restore_customer_id is not None:
        if payload.create_new_identity or payload.restore_customer_id not in {match["id"] for match in matches}:
            raise HTTPException(status_code=409, detail="The selected archived Customer does not match this first and last name.")
        customer = db.scalar(select(Customer).where(Customer.id == payload.restore_customer_id).with_for_update().execution_options(populate_existing=True))
        # Recheck after locking: concurrent restoration cannot invite two
        # accounts or turn a now-active Customer into a new restoration.
        if customer is None or customer.active or customer.deleted_at is None or db.scalar(select(CustomerTombstone.id).where(CustomerTombstone.former_customer_id == customer.id)) is not None:
            raise HTTPException(status_code=409, detail="This Customer has already been restored.")
    elif matches and not payload.create_new_identity:
        raise HTTPException(status_code=409, detail={
            "message": "Archived customer history matches this first and last name. Confirm which Customer to restore, or create a separate Customer.",
            "archivedCustomers": matches,
        })
    slug = customer.slug if customer is not None else _customer_slug(db, name, payload.slug)
    auth_user_id = await _dispatch_invitation(settings, payload)
    restored = customer is not None
    creation_reason = ("Operator approved archived Customer restoration and invited a new User Account"
                       if restored else "Created a Customer and invited its User Account")

    if customer is None:
        customer = Customer(slug=slug, name=name, licensed_states=[], agency_id=None, active=True)
        initialize_customer_billing(customer, settings)
        db.add(customer)
    else:
        customer.active = True
        customer.deleted_at = None
        if customer.agency is not None and (not customer.agency.active or customer.agency.deleted_at is not None):
            customer.agency_id = None
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
        action="customer_history_restored" if restored else "customer_created",
        target_type="customer",
        target_id=customer.id,
        actor_id=principal.user_id,
        reason=creation_reason,
        details={
            "before": {"archived": True} if restored else None,
            "after": {
                "slug": customer.slug,
                "name": customer.name,
                "active": True,
                "agencyId": customer.agency_id,
                "licensedStates": list(customer.licensed_states),
            },
            **({"operatorApproved": True, "historyPreserved": True} if restored else {}),
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
        "historyRestored": restored,
    }


from .operations import router as operations_router
from .health import router as health_router
from .nightly_reviews import router as nightly_reviews_router
from .auth import router as auth_router
from .exclusion_lists import router as exclusion_lists_router
from .me import router as me_router
from .billing import router as billing_router
from .acquisition import router as acquisition_router
from .fulfillment import router as fulfillment_router
from .customers import router as customers_router
from .inventory import router as inventory_router
from .leads import router as leads_router
from .scraper_config import router as scraper_config_router
from .webhooks import router as webhooks_router


from .customers import replace_user_account as replace_user_account


for _area_router in (
    operations_router,
    health_router,
    nightly_reviews_router,
    auth_router,
    exclusion_lists_router,
    me_router,
    billing_router,
    acquisition_router,
    fulfillment_router,
    customers_router,
    inventory_router,
    leads_router,
    scraper_config_router,
    webhooks_router,
):
    # FastAPI 0.140 defers include_router() behind a proxy that app.routes
    # enumeration cannot see through; registering the APIRoute objects
    # directly keeps the administrator-route inventory tests whole. The
    # handler closure captures the overrides provider at build time, so it
    # is rebuilt after pointing it at the app (what direct @app
    # registration does).
    for _route in _area_router.routes:
        if isinstance(_route, APIRoute):
            _route.dependency_overrides_provider = app
            _route.app = request_response(_route.get_route_handler())
        app.router.routes.append(_route)
del _area_router, _route
