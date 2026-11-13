from __future__ import annotations

import asyncio

from fastapi import Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

import jawnix.scraper_proxy as _scraper_proxy
from ..activity import record_activity
from ..admin_mfa_api import (
    _factors,
    _identity_for_token,
    _record_factor_use,
    _record_failure,
    _reset_failures,
    _session_json,
    _state as _admin_mfa_state,
    _throttle_status,
    _verified,
    _verify_factor,
)
from ..auth import Principal, require_admin
from ..config import Settings, get_settings
from ..database import get_db
from ..mfa_provider import MFAProviderError
from ..schemas import AdminMFAChallenge
from ..scraper_monitoring import RegionData, RegionKey, health_summary
from ..scraper_operations import ScraperOperationsError
from .core import (
    HEALTH_CACHE_SECONDS,
    _mark_operations_success,
    _native_unavailable,
    _safe_last_success,
    _scraper_operations,
    _state,
    _workspace_now,
    native_router,
)
from .grants import (
    _clear_grant,
    _has_grant,
    _issue_grant,
    _require_scraper_grant,
)


@native_router.post("/entry")
async def begin_scraper_entry(
    request: Request,
    response: Response,
    principal: Principal = Depends(require_admin),
    settings: Settings = Depends(get_settings),
):
    """Reuse this sign-in's live grant, or present the factor challenge.

    One verification per sign-in: entering again while the grant is live goes
    straight to the workspace instead of asking for another code.
    """

    if _has_grant(request, principal, settings):
        return {"granted": True, "factors": []}
    _clear_grant(response, settings)
    provider = _scraper_proxy.get_mfa_provider(settings)
    factors = _verified(await _factors(provider, principal))
    return {
        "granted": False,
        "factors": [
            {
                "id": str(factor.id),
                "name": factor.friendly_name,
                "type": factor.factor_type,
            }
            for factor in factors
        ],
    }


@native_router.get("/grant")
async def scraper_grant_status(
    request: Request,
    principal: Principal = Depends(require_admin),
    settings: Settings = Depends(get_settings),
):
    """Whether this sign-in already holds a live Scraper grant (for Setup)."""
    return {"granted": _has_grant(request, principal, settings)}


@native_router.post("/step-up")
async def complete_scraper_step_up(
    payload: AdminMFAChallenge,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    """Require a real provider challenge; an existing AAL2 claim is insufficient."""

    state = _admin_mfa_state(db, principal.user_id)
    throttled, _ = _throttle_status(state, settings)
    if throttled:
        db.commit()
        raise HTTPException(
            status_code=429,
            detail="Too many attempts. Try again after the stated time.",
            headers={"Retry-After": str(settings.admin_mfa_lock_seconds)},
        )
    provider = _scraper_proxy.get_mfa_provider(settings)
    await _identity_for_token(provider, principal, payload.access_token)
    factors = await _factors(provider, principal)
    if payload.factor_id not in {
        factor.id for factor in _verified(factors)
    }:
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
            method=payload.method,
        )
    except MFAProviderError as exc:
        if not exc.invalid_code:
            record_activity(
                db,
                action="scraper_step_up_unavailable",
                target_type="scraper_privileged_session",
                target_id=principal.user_id,
                actor_id=principal.user_id,
                reason="Fresh Scraper authenticator challenge was unavailable",
            )
            db.commit()
            raise HTTPException(
                status_code=503,
                detail="Scraper verification is temporarily unavailable.",
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
    _, claims = await _identity_for_token(
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
    record_activity(
        db,
        action="scraper_step_up_succeeded",
        target_type="scraper_privileged_session",
        target_id=principal.user_id,
        actor_id=principal.user_id,
        reason="Fresh authenticator challenge completed for Scraper Operations",
        details={"lasts": "until sign-out or administrator session expiry"},
        known_secrets=(payload.access_token, payload.code),
    )
    db.commit()
    _issue_grant(response, principal, settings)
    return {
        "ok": True,
        "session": _session_json(provider_session),
    }


@native_router.get("/workspace")
async def scraper_workspace(
    request: Request,
    response: Response,
    principal: Principal = Depends(require_admin),
    settings: Settings = Depends(get_settings),
):
    _require_scraper_grant(
        request,
        response,
        principal,
        settings,
    )
    state = _state(request, settings)
    try:
        await _scraper_operations(request, settings).workspace_summary()
    except ScraperOperationsError:
        return _native_unavailable(state)
    _mark_operations_success(request, settings)
    return {
        "serviceState": "connected",
        "lastSuccessfulAt": _safe_last_success(state),
    }


@native_router.get("/health")
async def scraper_health(
    request: Request,
    principal: Principal = Depends(require_admin),
    settings: Settings = Depends(get_settings),
):
    """Today's one-line Scraper health, for a sign-in holding the grant.

    It lives under the workspace's path so the grant cookie reaches it, and
    it asks the same question every workspace read does. Without a live
    grant it answers ``granted: false`` and the Scraper is never called:
    Today then links to the Scraper entry instead of bypassing it. With one,
    the activity and workers regions are read through the workspace's own
    operations client, at most once per ``HEALTH_CACHE_SECONDS`` however
    often Today refreshes, and a failed read is cached the same way so an
    outage does not make every refresh wait out the Scraper timeout.
    """

    if not _has_grant(request, principal, settings):
        return {"granted": False}
    state = _state(request, settings)
    async with state.health_lock:
        now = _workspace_now(request)
        if (
            state.health is None
            or state.health_read_at is None
            or (now - state.health_read_at).total_seconds()
            >= HEALTH_CACHE_SECONDS
        ):
            operations = _scraper_operations(request, settings)

            async def read(region: RegionKey) -> RegionData | None:
                try:
                    return await operations.monitoring_region(region)
                except ScraperOperationsError:
                    return None

            activity, workers = await asyncio.gather(
                read("activity"),
                read("workers"),
            )
            state.health = health_summary(activity, workers, checked_at=now)
            state.health_read_at = now
            if state.health["serviceState"] == "connected":
                _mark_operations_success(request, settings)
        health = state.health
    return {
        "granted": True,
        **health,
        "lastSuccessfulAt": _safe_last_success(state),
    }
