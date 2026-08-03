from __future__ import annotations

import hmac
import uuid
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, Response, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .database import get_db
from .mfa_provider import MFAProviderError, get_mfa_provider
from .models import AdminMFAState


SESSION_COOKIE = "jawnix_session"
CSRF_COOKIE = "jawnix_csrf"


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    email: str
    role: str
    csrf: str
    assurance: str = "aal1"
    audience: str = "customer"
    session_generation: int = 0
    factor_id: uuid.UUID | None = None


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret, salt="jawnix-vps-session-v1")


async def verify_supabase_token(access_token: str, settings: Settings) -> dict:
    try:
        user, claims = await get_mfa_provider(settings).user_for_token(access_token)
    except MFAProviderError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from None
    # Keep the existing return shape for callers and tests while carrying the
    # provider-verified JWT claims beside it for session issuance.
    return {**user, "_jawnix_auth_claims": claims}


def issue_session(
    response: Response,
    user: dict,
    settings: Settings,
    *,
    assurance: str | None = None,
    session_generation: int = 0,
    factor_id: uuid.UUID | None = None,
) -> Principal:
    user_id = uuid.UUID(str(user["id"]))
    email = str(user.get("email") or "").strip().lower()
    role = str((user.get("app_metadata") or {}).get("jawnix_role") or "customer")
    audience = "admin" if role == "admin" else "customer"
    claims = user.get("_jawnix_auth_claims") or {}
    established_assurance = assurance or str(claims.get("aal") or "aal1")
    csrf = uuid.uuid4().hex
    token = _serializer(settings).dumps(
        {
            "sub": str(user_id),
            "email": email,
            "role": role,
            "aud": audience,
            "aal": established_assurance,
            "generation": session_generation,
            "factor_id": str(factor_id) if factor_id else None,
            "csrf": csrf,
        }
    )
    cookie_common = {
        "secure": settings.cookie_secure,
        "samesite": "lax",
        "path": "/",
        "max_age": settings.session_ttl_seconds,
    }
    response.set_cookie(SESSION_COOKIE, token, httponly=True, **cookie_common)
    response.set_cookie(CSRF_COOKIE, csrf, httponly=False, **cookie_common)
    return Principal(
        user_id=user_id,
        email=email,
        role=role,
        csrf=csrf,
        assurance=established_assurance,
        audience=audience,
        session_generation=session_generation,
        factor_id=factor_id,
    )


def clear_session(response: Response, settings: Settings) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/", secure=settings.cookie_secure, samesite="lax")
    response.delete_cookie(CSRF_COOKIE, path="/", secure=settings.cookie_secure, samesite="lax")


def principal_from_request(request: Request, settings: Settings) -> Principal:
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token:
        raise HTTPException(status_code=401, detail="Login required.")
    try:
        payload = _serializer(settings).loads(token, max_age=settings.session_ttl_seconds)
        return Principal(
            user_id=uuid.UUID(str(payload["sub"])),
            email=str(payload["email"]),
            role=str(payload["role"]),
            csrf=str(payload["csrf"]),
            assurance=str(payload.get("aal") or "aal1"),
            audience=str(
                payload.get("aud")
                or ("admin" if payload.get("role") == "admin" else "customer")
            ),
            session_generation=int(payload.get("generation") or 0),
            factor_id=(
                uuid.UUID(str(payload["factor_id"]))
                if payload.get("factor_id")
                else None
            ),
        )
    except (BadSignature, SignatureExpired, KeyError, ValueError):
        raise HTTPException(status_code=401, detail="Session expired.") from None


def require_principal(request: Request, settings: Settings = Depends(get_settings)) -> Principal:
    principal = principal_from_request(request, settings)
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        header = request.headers.get("X-CSRF-Token", "")
        cookie = request.cookies.get(CSRF_COOKIE, "")
        if not header or not cookie or not hmac.compare_digest(header, cookie) or not hmac.compare_digest(header, principal.csrf):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF validation failed.")
    return principal


def require_admin_identity(
    principal: Principal = Depends(require_principal),
) -> Principal:
    """Authorize the enrollment-only surface without granting administration."""

    if principal.role != "admin" or principal.audience != "admin":
        raise HTTPException(status_code=403, detail="Admin access required.")
    return principal


async def require_admin(
    principal: Principal = Depends(require_principal),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
) -> Principal:
    if principal.role != "admin" or principal.audience != "admin":
        raise HTTPException(status_code=403, detail="Admin access required.")
    state = db.get(AdminMFAState, principal.user_id)
    if state is None or principal.session_generation != state.session_generation:
        raise HTTPException(
            status_code=401,
            detail="This administrator session is no longer valid.",
            headers={"X-Jawnix-Auth-Next": "/app/admin/mfa/enroll"},
        )
    try:
        factors = await get_mfa_provider(settings).list_factors(
            principal.user_id
        )
    except MFAProviderError:
        # Live state is necessary, not advisory.  Provider failure therefore
        # fails closed instead of falling back to the signed cookie claim.
        raise HTTPException(
            status_code=503,
            detail="Administrator verification is temporarily unavailable.",
        ) from None
    verified = [factor for factor in factors if factor.verified_totp]
    if len(verified) < 2:
        raise HTTPException(
            status_code=401,
            detail="Administrator MFA enrollment is required.",
            headers={"X-Jawnix-Auth-Next": "/app/admin/mfa/enroll"},
        )
    if principal.assurance != "aal2":
        raise HTTPException(
            status_code=401,
            detail="Administrator verification is required.",
            headers={"X-Jawnix-Auth-Next": "/app/admin/mfa/challenge"},
        )
    live_ids = {factor.id for factor in verified}
    if principal.factor_id is not None and principal.factor_id not in live_ids:
        raise HTTPException(
            status_code=401,
            detail="The factor used by this session is no longer active.",
            headers={"X-Jawnix-Auth-Next": "/app/admin/mfa/challenge"},
        )
    return principal
