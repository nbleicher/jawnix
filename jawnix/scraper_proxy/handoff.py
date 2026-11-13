from __future__ import annotations

import html
import hmac
import uuid
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from itsdangerous import BadSignature, SignatureExpired

from ..auth import Principal
from ..config import Settings
from .core import (
    HANDOFF_MAX_AGE_SECONDS,
    MOUNT_PREFIX,
    SCRAPER_SESSION_COOKIE,
    _handoff_serializer,
    _scraper_serializer,
)


def request_is_scraper_origin(
    request: Request,
    settings: Settings,
) -> bool:
    expected = urlsplit(settings.scraper_ops_origin).netloc.lower()
    return bool(expected) and request.headers.get("host", "").lower() == expected


def scraper_handoff_response(
    principal: Principal,
    settings: Settings,
) -> HTMLResponse:
    origin = settings.scraper_ops_origin.rstrip("/")
    if not origin:
        raise HTTPException(
            status_code=503,
            detail="Scraper Operations origin is not configured.",
        )
    handoff = _handoff_serializer(settings).dumps(
        {
            "sub": str(principal.user_id),
            "email": principal.email,
            "role": principal.role,
            "csrf": uuid.uuid4().hex,
        }
    )
    action = html.escape(f"{origin}{MOUNT_PREFIX}/session", quote=True)
    token = html.escape(handoff, quote=True)
    response = HTMLResponse(
        content=f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Opening Scraper Operations</title></head>
<body onload="document.forms[0].submit()">
<main><h1>Opening Scraper Operations</h1>
<form method="post" action="{action}">
<input type="hidden" name="handoff" value="{token}">
<button type="submit">Continue</button>
</form></main></body></html>""",
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'none'; script-src 'unsafe-inline'; "
                f"form-action {origin}; base-uri 'none'; frame-ancestors 'none'"
            ),
            "Referrer-Policy": "no-referrer",
        },
    )
    return response


async def accept_scraper_handoff(
    request: Request,
    settings: Settings,
) -> Response:
    if not request_is_scraper_origin(request, settings):
        raise HTTPException(status_code=404, detail="Not found.")
    form = await request.form()
    try:
        payload = _handoff_serializer(settings).loads(
            str(form.get("handoff") or ""),
            max_age=HANDOFF_MAX_AGE_SECONDS,
        )
        principal = Principal(
            user_id=uuid.UUID(str(payload["sub"])),
            email=str(payload["email"]),
            role=str(payload["role"]),
            csrf=str(payload["csrf"]),
        )
    except (BadSignature, SignatureExpired, KeyError, ValueError):
        raise HTTPException(
            status_code=401,
            detail="Scraper Operations handoff expired.",
        ) from None
    if principal.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required.")

    response = RedirectResponse(f"{MOUNT_PREFIX}/", status_code=303)
    response.set_cookie(
        SCRAPER_SESSION_COOKIE,
        _scraper_serializer(settings).dumps(payload),
        secure=settings.cookie_secure,
        httponly=True,
        samesite="strict",
        path=MOUNT_PREFIX,
        max_age=settings.session_ttl_seconds,
    )
    return response


def clear_scraper_session(
    request: Request,
    settings: Settings,
) -> Response:
    if not request_is_scraper_origin(request, settings):
        raise HTTPException(status_code=404, detail="Not found.")
    primary = urlsplit(settings.public_base_url)
    expected_origin = f"{primary.scheme}://{primary.netloc}"
    if request.headers.get("origin") != expected_origin:
        raise HTTPException(status_code=403, detail="Invalid logout origin.")
    response = Response(status_code=204)
    response.delete_cookie(
        SCRAPER_SESSION_COOKIE,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="strict",
        path=MOUNT_PREFIX,
    )
    return response


def scraper_principal_from_request(
    request: Request,
    settings: Settings,
) -> Principal:
    token = request.cookies.get(SCRAPER_SESSION_COOKIE, "")
    if not token:
        raise HTTPException(status_code=401, detail="Login required.")
    try:
        payload = _scraper_serializer(settings).loads(
            token,
            max_age=settings.session_ttl_seconds,
        )
        principal = Principal(
            user_id=uuid.UUID(str(payload["sub"])),
            email=str(payload["email"]),
            role=str(payload["role"]),
            csrf=str(payload["csrf"]),
        )
    except (BadSignature, SignatureExpired, KeyError, ValueError):
        raise HTTPException(status_code=401, detail="Session expired.") from None
    if principal.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required.")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        supplied = request.headers.get("X-Scraper-CSRF", "")
        if not supplied or not hmac.compare_digest(supplied, principal.csrf):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="CSRF validation failed.",
            )
    return principal
