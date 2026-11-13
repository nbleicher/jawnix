from __future__ import annotations

import hashlib
import hmac
import uuid

from fastapi import HTTPException, Request
from fastapi.responses import Response
from itsdangerous import BadSignature, SignatureExpired

from ..auth import Principal
from ..config import Settings
from .core import SCRAPER_GRANT_COOKIE, _grant_serializer


def _clear_grant(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        SCRAPER_GRANT_COOKIE,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="strict",
        path="/api/admin/scraper",
    )


def clear_scraper_grant(response: Response, settings: Settings) -> None:
    """End the Scraper Privileged Session with the sign-in it belongs to."""
    _clear_grant(response, settings)


def _session_binding(principal: Principal) -> str:
    """Names the Administrator Session a grant belongs to.

    Each sign-in issues a fresh CSRF token, so its digest identifies that
    session without putting the token itself in a second cookie. A grant from
    an earlier sign-in therefore never opens the workspace for a later one.
    """
    return hashlib.sha256(
        f"jawnix-scraper-grant:{principal.csrf}".encode()
    ).hexdigest()


def _issue_grant(
    response: Response,
    principal: Principal,
    settings: Settings,
) -> None:
    token = _grant_serializer(settings).dumps(
        {
            "sub": str(principal.user_id),
            "generation": principal.session_generation,
            "session": _session_binding(principal),
        }
    )
    response.set_cookie(
        SCRAPER_GRANT_COOKIE,
        token,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="strict",
        path="/api/admin/scraper",
        # Never outlives the Administrator Session it was verified in.
        max_age=settings.session_ttl_seconds,
    )


class _GrantRefused(Exception):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


_FRESH_CHALLENGE = "A fresh Scraper authenticator challenge is required."


def _check_grant(
    request: Request,
    principal: Principal,
    settings: Settings,
) -> None:
    """Raise ``_GrantRefused`` unless this sign-in holds a live grant.

    A grant lasts until sign-out or Administrator Session expiry: it must name
    this administrator, this session generation (revoked by "Sign out
    everywhere" and factor changes), and this exact sign-in.
    """
    token = request.cookies.get(SCRAPER_GRANT_COOKIE, "")
    if not token:
        raise _GrantRefused(_FRESH_CHALLENGE)
    try:
        payload = _grant_serializer(settings).loads(
            token,
            max_age=settings.session_ttl_seconds,
        )
        issued_to = uuid.UUID(str(payload["sub"]))
        generation = int(payload["generation"])
        session = str(payload["session"])
    except SignatureExpired:
        raise _GrantRefused("Scraper privileged session expired.") from None
    except (BadSignature, KeyError, TypeError, ValueError, OverflowError):
        raise _GrantRefused(_FRESH_CHALLENGE) from None
    if (
        issued_to != principal.user_id
        or generation != principal.session_generation
        or not hmac.compare_digest(session, _session_binding(principal))
    ):
        raise _GrantRefused(_FRESH_CHALLENGE)


def _require_scraper_grant(
    request: Request,
    response: Response,
    principal: Principal,
    settings: Settings,
) -> None:
    try:
        _check_grant(request, principal, settings)
    except _GrantRefused as refused:
        _clear_grant(response, settings)
        raise HTTPException(status_code=401, detail=refused.detail) from None


def _has_grant(request: Request, principal: Principal, settings: Settings) -> bool:
    try:
        _check_grant(request, principal, settings)
    except _GrantRefused:
        return False
    return True
