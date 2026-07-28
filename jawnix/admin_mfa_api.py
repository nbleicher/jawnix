"""Administrator MFA enrollment, challenge, replacement, and revocation API."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .activity import record_activity
from .auth import (
    Principal,
    clear_session,
    issue_session,
    require_admin,
    require_admin_identity,
)
from .config import Settings, get_settings
from .database import get_db
from .mfa_provider import (
    MFAProviderError,
    ProviderFactor,
    ProviderSession,
    SupabaseMFAProvider,
    get_mfa_provider,
)
from .models import AdminMFAFactorUse, AdminMFAState, utcnow
from .schemas import (
    AdminMFAAccessToken,
    AdminMFAChallenge,
    AdminMFACode,
    AdminMFAEnrollStart,
    AdminMFAReplacementStart,
)


router = APIRouter(prefix="/api/auth/admin-mfa", tags=["administrator-mfa"])


def _enabled(settings: Settings) -> None:
    if not settings.new_ui_enabled:
        raise HTTPException(status_code=404, detail="Not found.")


def _state(db: Session, user_id: uuid.UUID) -> AdminMFAState:
    # Serialize challenge counters and enrollment transitions per account.  A
    # concurrent pair of failures must not both observe the same old count.
    value = db.scalar(
        select(AdminMFAState)
        .where(AdminMFAState.user_id == user_id)
        .with_for_update()
    )
    if value is None:
        value = AdminMFAState(user_id=user_id, session_generation=1)
        db.add(value)
        db.flush()
    return value


def _verified(factors: list[ProviderFactor]) -> list[ProviderFactor]:
    return [factor for factor in factors if factor.verified_totp]


def _location(request: Request) -> tuple[str, str]:
    ip_address = request.client.host if request.client else "unknown"
    user_agent = (request.headers.get("user-agent") or "unknown")[:320]
    return ip_address[:80], user_agent


def _audit(
    db: Session,
    principal: Principal,
    action: str,
    reason: str,
    details: dict | None = None,
) -> None:
    record_activity(
        db,
        action=action,
        target_type="administrator_mfa",
        target_id=principal.user_id,
        actor_id=principal.user_id,
        reason=reason,
        details=details,
    )


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _throttle_status(
    state: AdminMFAState,
    settings: Settings,
) -> tuple[bool, datetime | None]:
    now = utcnow()
    locked_until = _as_utc(state.locked_until)
    if locked_until and locked_until > now:
        return True, locked_until
    window_started = _as_utc(state.failure_window_started_at)
    if (
        window_started
        and now - window_started
        > timedelta(seconds=settings.admin_mfa_attempt_window_seconds)
    ):
        state.failed_attempts = 0
        state.failure_window_started_at = None
        state.locked_until = None
    return False, None


def _record_failure(
    db: Session,
    state: AdminMFAState,
    settings: Settings,
    principal: Principal,
    request: Request,
) -> datetime | None:
    now = utcnow()
    if state.failure_window_started_at is None:
        state.failure_window_started_at = now
        state.failed_attempts = 0
    state.failed_attempts += 1
    if state.failed_attempts >= settings.admin_mfa_max_attempts:
        state.locked_until = now + timedelta(
            seconds=settings.admin_mfa_lock_seconds
        )
    ip_address, user_agent = _location(request)
    _audit(
        db,
        principal,
        "admin_mfa_challenge_failed",
        "Authenticator challenge was refused",
        {
            "ipAddress": ip_address,
            "userAgent": user_agent,
            "throttled": state.locked_until is not None,
        },
    )
    db.commit()
    return _as_utc(state.locked_until)


def _reset_failures(state: AdminMFAState) -> None:
    state.failed_attempts = 0
    state.failure_window_started_at = None
    state.locked_until = None


def _record_factor_use(
    db: Session,
    principal: Principal,
    factor_id: uuid.UUID,
    request: Request,
) -> None:
    ip_address, user_agent = _location(request)
    value = db.get(AdminMFAFactorUse, factor_id)
    if value is None:
        value = AdminMFAFactorUse(
            provider_factor_id=factor_id,
            user_id=principal.user_id,
        )
        db.add(value)
    value.user_id = principal.user_id
    value.last_used_at = utcnow()
    value.ip_address = ip_address
    value.user_agent = user_agent


async def _identity_for_token(
    provider: SupabaseMFAProvider,
    principal: Principal,
    access_token: str,
) -> tuple[dict, dict]:
    try:
        user, claims = await provider.user_for_token(access_token)
    except MFAProviderError:
        raise HTTPException(
            status_code=401,
            detail="The identity session is invalid or expired.",
        ) from None
    role = str((user.get("app_metadata") or {}).get("jawnix_role") or "")
    if str(user.get("id") or "") != str(principal.user_id) or role != "admin":
        # Do not disclose which part mismatched.
        raise HTTPException(
            status_code=401,
            detail="The identity session is invalid or expired.",
        )
    return user, claims


async def _factors(
    provider: SupabaseMFAProvider,
    principal: Principal,
) -> list[ProviderFactor]:
    try:
        return await provider.list_factors(principal.user_id)
    except MFAProviderError:
        raise HTTPException(
            status_code=503,
            detail="Administrator verification is temporarily unavailable.",
        ) from None


def _factor_json(
    factor: ProviderFactor,
    use: AdminMFAFactorUse | None,
) -> dict:
    return {
        "id": str(factor.id),
        "name": factor.friendly_name,
        "status": factor.status,
        "type": factor.factor_type,
        "createdAt": factor.created_at,
        "lastUsedAt": (
            use.last_used_at
            if use is not None
            else factor.last_challenged_at
        ),
        "lastUsedFrom": (
            {
                "ipAddress": use.ip_address,
                "userAgent": use.user_agent,
            }
            if use is not None
            else None
        ),
    }


def _session_json(value: ProviderSession) -> dict:
    # These provider credentials are intentionally returned only to the
    # authenticated browser that supplied the previous credential.  The React
    # client passes them to Supabase's setSession so refresh/resumption works.
    return {
        "accessToken": value.access_token,
        "refreshToken": value.refresh_token,
        "expiresIn": value.expires_in,
    }


@router.get("")
async def admin_mfa_status(
    principal: Principal = Depends(require_admin_identity),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    _enabled(settings)
    state = _state(db, principal.user_id)
    provider = get_mfa_provider(settings)
    factors = await _factors(provider, principal)
    uses = {
        value.provider_factor_id: value
        for value in db.scalars(
            select(AdminMFAFactorUse).where(
                AdminMFAFactorUse.user_id == principal.user_id
            )
        )
    }
    throttled, locked_until = _throttle_status(state, settings)
    db.commit()
    verified = _verified(factors)
    if len(verified) < 2:
        next_path = "/app/admin/mfa/enroll"
    elif principal.assurance != "aal2":
        next_path = "/app/admin/mfa/challenge"
    else:
        next_path = "/app/admin/overview"
    return {
        "assurance": principal.assurance,
        "enforced": len(verified) >= 2,
        "stage": state.enrollment_stage,
        "factors": [
            _factor_json(factor, uses.get(factor.id)) for factor in factors
        ],
        "throttled": throttled,
        "lockedUntil": locked_until,
        "next": next_path,
    }


@router.get("/access")
async def admin_mfa_access(
    _: Principal = Depends(require_admin),
    settings: Settings = Depends(get_settings),
):
    """A cheap loader target that exercises the real administrator boundary."""

    _enabled(settings)
    return {"ok": True}


@router.post("/enrollment")
async def start_admin_mfa_enrollment(
    payload: AdminMFAEnrollStart,
    principal: Principal = Depends(require_admin_identity),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    _enabled(settings)
    provider = get_mfa_provider(settings)
    _, claims = await _identity_for_token(
        provider,
        principal,
        payload.access_token,
    )
    factors = await _factors(provider, principal)
    verified = _verified(factors)
    expected_slot = "primary" if not verified else "backup"
    if payload.slot != expected_slot or len(verified) >= 2:
        raise HTTPException(
            status_code=409,
            detail="Authenticator enrollment is not at that step.",
        )
    if verified and str(claims.get("aal") or "aal1") != "aal2":
        raise HTTPException(
            status_code=401,
            detail="Verify the enrolled authenticator before adding the backup.",
        )

    state = _state(db, principal.user_id)
    if state.enrollment_stage == "idle":
        state.enrollment_baseline_factor_ids = [
            str(factor.id) for factor in factors
        ]
        state.enrollment_new_factor_ids = []
    if state.active_factor_id is not None:
        # A QR secret is intentionally never persisted by Jawnix.  Restarting
        # an interrupted scan deletes the now-unrecoverable unverified factor.
        try:
            await provider.delete_factor(
                principal.user_id,
                state.active_factor_id,
            )
        except MFAProviderError:
            raise HTTPException(
                status_code=503,
                detail="Authenticator setup could not be restarted.",
            ) from None
    try:
        enrolled = await provider.enroll(
            payload.access_token,
            f"Jawnix {payload.slot}",
        )
    except MFAProviderError:
        raise HTTPException(
            status_code=502,
            detail="Authenticator setup could not be started.",
        ) from None
    state.active_factor_id = enrolled.id
    state.enrollment_stage = f"{payload.slot}_pending"
    state.enrollment_new_factor_ids = [
        *state.enrollment_new_factor_ids,
        str(enrolled.id),
    ]
    _audit(
        db,
        principal,
        "admin_mfa_enrollment_started",
        f"{payload.slot.capitalize()} authenticator enrollment started",
        {"slot": payload.slot},
    )
    db.commit()
    return {
        "factorId": str(enrolled.id),
        "slot": payload.slot,
        "qrCode": enrolled.qr_code,
        "manualKey": enrolled.secret,
        "uri": enrolled.uri,
    }


async def _verify_factor(
    *,
    provider: SupabaseMFAProvider,
    access_token: str,
    factor_id: uuid.UUID,
    code: str,
) -> ProviderSession:
    challenge_id = await provider.challenge(access_token, factor_id)
    return await provider.verify(
        access_token,
        factor_id,
        challenge_id,
        code,
    )


@router.post("/enrollment/verify")
async def verify_admin_mfa_enrollment(
    payload: AdminMFACode,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_admin_identity),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    _enabled(settings)
    state = _state(db, principal.user_id)
    throttled, locked_until = _throttle_status(state, settings)
    if throttled:
        db.commit()
        raise HTTPException(
            status_code=429,
            detail="Too many attempts. Try again after the stated time.",
            headers={"Retry-After": str(settings.admin_mfa_lock_seconds)},
        )
    if state.active_factor_id is None:
        raise HTTPException(
            status_code=409,
            detail="No authenticator enrollment is awaiting verification.",
        )
    provider = get_mfa_provider(settings)
    await _identity_for_token(provider, principal, payload.access_token)
    factor_id = state.active_factor_id
    try:
        provider_session = await _verify_factor(
            provider=provider,
            access_token=payload.access_token,
            factor_id=factor_id,
            code=payload.code,
        )
    except MFAProviderError as exc:
        if not exc.invalid_code:
            _audit(
                db,
                principal,
                "admin_mfa_challenge_unavailable",
                "Authenticator challenge service was unavailable",
            )
            db.commit()
            raise HTTPException(
                status_code=503,
                detail="Administrator verification is temporarily unavailable.",
            ) from None
        locked_until = _record_failure(
            db,
            state,
            settings,
            principal,
            request,
        )
        if locked_until:
            raise HTTPException(
                status_code=429,
                detail="Too many attempts. Try again after the stated time.",
                headers={"Retry-After": str(settings.admin_mfa_lock_seconds)},
            ) from None
        raise HTTPException(
            status_code=422,
            detail="That code could not be verified. Check it and try again.",
        ) from None

    user, claims = await _identity_for_token(
        provider,
        principal,
        provider_session.access_token,
    )
    if str(claims.get("aal") or "") != "aal2":
        raise HTTPException(
            status_code=502,
            detail="The verification service returned an incomplete session.",
        )
    replacement_id = state.replacement_factor_id
    if replacement_id is not None:
        try:
            await provider.delete_factor(principal.user_id, replacement_id)
        except MFAProviderError:
            raise HTTPException(
                status_code=502,
                detail="The lost authenticator could not be replaced.",
            ) from None

    state.active_factor_id = None
    state.replacement_factor_id = None
    state.session_generation += 1
    _reset_failures(state)
    _record_factor_use(db, principal, factor_id, request)
    factors = await _factors(provider, principal)
    complete = len(_verified(factors)) >= 2
    state.enrollment_stage = "complete" if complete else "primary_verified"
    action = (
        "admin_mfa_factor_replaced"
        if replacement_id is not None
        else "admin_mfa_factor_verified"
    )
    _audit(
        db,
        principal,
        action,
        (
            "Lost authenticator replaced"
            if replacement_id is not None
            else "Authenticator enrollment verified"
        ),
        {"complete": complete},
    )
    db.commit()
    issued = issue_session(
        response,
        user,
        settings,
        assurance="aal2",
        session_generation=state.session_generation,
        factor_id=factor_id,
    )
    return {
        "ok": True,
        "complete": complete,
        "next": (
            "/app/admin/overview"
            if complete
            else "/app/admin/mfa/enroll"
        ),
        "session": _session_json(provider_session),
        "assurance": issued.assurance,
    }


@router.post("/challenge")
async def challenge_admin_mfa(
    payload: AdminMFAChallenge,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_admin_identity),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    _enabled(settings)
    state = _state(db, principal.user_id)
    throttled, _ = _throttle_status(state, settings)
    if throttled:
        db.commit()
        raise HTTPException(
            status_code=429,
            detail="Too many attempts. Try again after the stated time.",
            headers={"Retry-After": str(settings.admin_mfa_lock_seconds)},
        )
    provider = get_mfa_provider(settings)
    await _identity_for_token(provider, principal, payload.access_token)
    factors = await _factors(provider, principal)
    verified_ids = {factor.id for factor in _verified(factors)}
    if payload.factor_id not in verified_ids:
        raise HTTPException(
            status_code=422,
            detail="That authenticator is unavailable.",
        )
    try:
        provider_session = await _verify_factor(
            provider=provider,
            access_token=payload.access_token,
            factor_id=payload.factor_id,
            code=payload.code,
        )
    except MFAProviderError as exc:
        if not exc.invalid_code:
            _audit(
                db,
                principal,
                "admin_mfa_challenge_unavailable",
                "Authenticator challenge service was unavailable",
            )
            db.commit()
            raise HTTPException(
                status_code=503,
                detail="Administrator verification is temporarily unavailable.",
            ) from None
        locked_until = _record_failure(
            db,
            state,
            settings,
            principal,
            request,
        )
        if locked_until:
            raise HTTPException(
                status_code=429,
                detail="Too many attempts. Try again after the stated time.",
                headers={"Retry-After": str(settings.admin_mfa_lock_seconds)},
            ) from None
        raise HTTPException(
            status_code=422,
            detail="That code could not be verified. Check it and try again.",
        ) from None
    user, claims = await _identity_for_token(
        provider,
        principal,
        provider_session.access_token,
    )
    if str(claims.get("aal") or "") != "aal2":
        raise HTTPException(
            status_code=502,
            detail="The verification service returned an incomplete session.",
        )
    _reset_failures(state)
    _record_factor_use(db, principal, payload.factor_id, request)
    _audit(
        db,
        principal,
        "admin_mfa_challenge_succeeded",
        "Administrator challenge completed",
        dict(zip(("ipAddress", "userAgent"), _location(request))),
    )
    db.commit()
    issue_session(
        response,
        user,
        settings,
        assurance="aal2",
        session_generation=state.session_generation,
        factor_id=payload.factor_id,
    )
    return {
        "ok": True,
        "next": "/app/admin/overview",
        "session": _session_json(provider_session),
    }


@router.post("/enrollment/cancel")
async def cancel_admin_mfa_enrollment(
    payload: AdminMFAAccessToken,
    response: Response,
    principal: Principal = Depends(require_admin_identity),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    _enabled(settings)
    provider = get_mfa_provider(settings)
    user, _ = await _identity_for_token(
        provider,
        principal,
        payload.access_token,
    )
    state = _state(db, principal.user_id)
    baseline = set(state.enrollment_baseline_factor_ids)
    for value in list(state.enrollment_new_factor_ids):
        if value in baseline:
            continue
        try:
            await provider.delete_factor(principal.user_id, uuid.UUID(value))
        except (MFAProviderError, ValueError):
            raise HTTPException(
                status_code=502,
                detail="Authenticator setup could not be cancelled safely.",
            ) from None
    state.session_generation += 1
    state.enrollment_stage = "idle"
    state.enrollment_baseline_factor_ids = []
    state.enrollment_new_factor_ids = []
    state.active_factor_id = None
    state.replacement_factor_id = None
    _reset_failures(state)
    _audit(
        db,
        principal,
        "admin_mfa_enrollment_cancelled",
        "Authenticator enrollment cancelled",
    )
    db.commit()
    issue_session(
        response,
        user,
        settings,
        assurance="aal1",
        session_generation=state.session_generation,
    )
    return {"ok": True, "next": "/app/admin/mfa/enroll"}


@router.post("/replacement")
async def start_admin_mfa_replacement(
    payload: AdminMFAReplacementStart,
    principal: Principal = Depends(require_admin_identity),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    _enabled(settings)
    provider = get_mfa_provider(settings)
    _, claims = await _identity_for_token(
        provider,
        principal,
        payload.access_token,
    )
    if str(claims.get("aal") or "") != "aal2":
        raise HTTPException(
            status_code=401,
            detail="Verify the authenticator you still hold first.",
        )
    factors = await _factors(provider, principal)
    verified_ids = {factor.id for factor in _verified(factors)}
    if payload.lost_factor_id not in verified_ids or len(verified_ids) < 2:
        raise HTTPException(
            status_code=409,
            detail="That authenticator cannot be replaced from this session.",
        )
    state = _state(db, principal.user_id)
    if state.active_factor_id is not None:
        raise HTTPException(
            status_code=409,
            detail="Finish or cancel the current authenticator setup first.",
        )
    try:
        enrolled = await provider.enroll(
            payload.access_token,
            "Jawnix replacement",
        )
    except MFAProviderError:
        raise HTTPException(
            status_code=502,
            detail="Replacement authenticator setup could not be started.",
        ) from None
    state.enrollment_baseline_factor_ids = [
        str(factor.id) for factor in factors
    ]
    state.enrollment_new_factor_ids = [str(enrolled.id)]
    state.active_factor_id = enrolled.id
    state.replacement_factor_id = payload.lost_factor_id
    state.enrollment_stage = "replacement_pending"
    _audit(
        db,
        principal,
        "admin_mfa_replacement_started",
        "Lost authenticator replacement started",
    )
    db.commit()
    return {
        "factorId": str(enrolled.id),
        "slot": "replacement",
        "qrCode": enrolled.qr_code,
        "manualKey": enrolled.secret,
        "uri": enrolled.uri,
    }


@router.post("/logout-everywhere")
async def logout_admin_everywhere(
    payload: AdminMFAAccessToken,
    response: Response,
    principal: Principal = Depends(require_admin_identity),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    _enabled(settings)
    provider = get_mfa_provider(settings)
    await _identity_for_token(provider, principal, payload.access_token)
    provider_failed = False
    try:
        await provider.logout(payload.access_token)
    except MFAProviderError:
        # Local revocation still completes; reporting partial provider failure
        # cannot be allowed to keep Jawnix cookies alive.
        provider_failed = True
    state = _state(db, principal.user_id)
    state.session_generation += 1
    _audit(
        db,
        principal,
        "admin_mfa_sessions_revoked",
        "Administrator signed out everywhere",
        {"providerLogoutCompleted": not provider_failed},
    )
    db.commit()
    clear_session(response, settings)
    return {"ok": True, "providerLogoutCompleted": not provider_failed}
