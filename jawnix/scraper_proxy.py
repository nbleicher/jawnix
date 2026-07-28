from __future__ import annotations

import html
import hmac
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from .activity import record_activity
from .admin_mfa_api import (
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
from .auth import Principal, require_admin
from .config import Settings, get_settings
from .database import get_db
from .mfa_provider import MFAProviderError, get_mfa_provider
from .schemas import AdminMFAChallenge


MOUNT_PREFIX = "/admin/scraper"
SCRAPER_SESSION_COOKIE = "jawnix_scraper_session"
HANDOFF_MAX_AGE_SECONDS = 60
SCRAPER_GRANT_COOKIE = "jawnix_scraper_grant"
SCRAPER_IDLE_SECONDS = 15 * 60
FORWARDED_REQUEST_HEADERS = {
    "accept",
    "accept-encoding",
    "content-type",
    "hx-current-url",
    "hx-request",
    "hx-target",
    "hx-trigger",
    "user-agent",
}
FORWARDED_RESPONSE_HEADERS = {
    "accept-ranges",
    "cache-control",
    "content-disposition",
    "content-type",
    "etag",
    "last-modified",
}
PATH_ATTRIBUTE = re.compile(
    r'(?P<attribute>(?:href|src|action|hx-(?:get|post|put|patch|delete)))'
    r'=(?P<quote>["\'])/(?P<path>(?!/)[^"\']*)'
)


@dataclass
class ScraperProxyState:
    settings_key: tuple[str, str, float]
    last_success: datetime | None = None


class PipelineControl(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["pause", "resume"]
    reason: str = Field(min_length=1, max_length=2000)


native_router = APIRouter(
    prefix="/api/admin/scraper",
    tags=["scraper-operations"],
)


def _scraper_serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        settings.session_secret,
        salt="jawnix-scraper-session-v1",
    )


def _handoff_serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        settings.session_secret,
        salt="jawnix-scraper-handoff-v1",
    )


def _grant_serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        settings.session_secret,
        salt="jawnix-scraper-privileged-v1",
    )


def _workspace_now(request: Request) -> datetime:
    clock = getattr(request.app.state, "scraper_workspace_clock", None)
    value = clock() if clock else datetime.now(timezone.utc)
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def _clear_grant(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        SCRAPER_GRANT_COOKIE,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="strict",
        path="/api/admin/scraper",
    )


def _issue_grant(
    response: Response,
    request: Request,
    principal: Principal,
    settings: Settings,
) -> None:
    token = _grant_serializer(settings).dumps(
        {
            "sub": str(principal.user_id),
            "generation": principal.session_generation,
            "last_activity": _workspace_now(request).timestamp(),
        }
    )
    response.set_cookie(
        SCRAPER_GRANT_COOKIE,
        token,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="strict",
        path="/api/admin/scraper",
        max_age=SCRAPER_IDLE_SECONDS,
    )


def _require_scraper_grant(
    request: Request,
    response: Response,
    principal: Principal,
    settings: Settings,
) -> None:
    token = request.cookies.get(SCRAPER_GRANT_COOKIE, "")
    if not token:
        raise HTTPException(
            status_code=401,
            detail="A fresh Scraper authenticator challenge is required.",
        )
    try:
        payload = _grant_serializer(settings).loads(token)
        issued_to = uuid.UUID(str(payload["sub"]))
        generation = int(payload["generation"])
        last_activity = datetime.fromtimestamp(
            float(payload["last_activity"]),
            tz=timezone.utc,
        )
    except (BadSignature, KeyError, TypeError, ValueError, OverflowError):
        _clear_grant(response, settings)
        raise HTTPException(
            status_code=401,
            detail="A fresh Scraper authenticator challenge is required.",
        ) from None
    if (
        issued_to != principal.user_id
        or generation != principal.session_generation
    ):
        _clear_grant(response, settings)
        raise HTTPException(
            status_code=401,
            detail="A fresh Scraper authenticator challenge is required.",
        )
    if (
        _workspace_now(request) - last_activity
        > timedelta(seconds=SCRAPER_IDLE_SECONDS)
    ):
        _clear_grant(response, settings)
        raise HTTPException(
            status_code=401,
            detail="Scraper privileged session expired.",
        )
    _issue_grant(response, request, principal, settings)


def _safe_last_success(state: ScraperProxyState) -> str | None:
    if state.last_success is None:
        return None
    return state.last_success.astimezone(timezone.utc).isoformat()


def _native_unavailable(state: ScraperProxyState) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "detail": "Scraper Operations is unavailable.",
            "lastSuccessfulAt": _safe_last_success(state),
        },
    )


async def _native_upstream(
    request: Request,
    settings: Settings,
    *,
    method: str,
    path: str,
    form: dict[str, str] | None = None,
) -> httpx.Response | None:
    state = _state(request, settings)
    if (
        not settings.scraper_ops_url
        or not settings.scraper_ops_user
        or not settings.scraper_ops_password
    ):
        return None
    transport = getattr(request.app.state, "scraper_proxy_transport", None)
    try:
        async with httpx.AsyncClient(
            base_url=settings.scraper_ops_url.rstrip("/"),
            auth=httpx.BasicAuth(
                settings.scraper_ops_user,
                settings.scraper_ops_password,
            ),
            timeout=settings.scraper_ops_timeout_seconds,
            follow_redirects=False,
            transport=transport,
        ) as client:
            upstream = await client.request(method, path, data=form)
    except httpx.RequestError:
        return None
    if not 200 <= upstream.status_code < 300:
        return None
    state.last_success = _workspace_now(request)
    return upstream


@native_router.post("/entry")
async def begin_scraper_entry(
    request: Request,
    response: Response,
    principal: Principal = Depends(require_admin),
    settings: Settings = Depends(get_settings),
):
    """Invalidate any prior grant before presenting a new factor challenge."""

    _clear_grant(response, settings)
    provider = get_mfa_provider(settings)
    factors = _verified(await _factors(provider, principal))
    return {
        "factors": [
            {
                "id": str(factor.id),
                "name": factor.friendly_name,
                "type": factor.factor_type,
            }
            for factor in factors
        ],
        "idleExpiresIn": SCRAPER_IDLE_SECONDS,
    }


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
    provider = get_mfa_provider(settings)
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
        details={"idleExpiresIn": SCRAPER_IDLE_SECONDS},
        known_secrets=(payload.access_token, payload.code),
    )
    db.commit()
    _issue_grant(response, request, principal, settings)
    return {
        "ok": True,
        "idleExpiresIn": SCRAPER_IDLE_SECONDS,
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
    upstream = await _native_upstream(
        request,
        settings,
        method="GET",
        path="/dashboard",
    )
    state = _state(request, settings)
    if upstream is None:
        return _native_unavailable(state)
    return {
        "serviceState": "connected",
        "lastSuccessfulAt": _safe_last_success(state),
        "idleExpiresIn": SCRAPER_IDLE_SECONDS,
    }


@native_router.post("/pipeline")
async def control_scraper_pipeline(
    payload: PipelineControl,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    _require_scraper_grant(
        request,
        response,
        principal,
        settings,
    )
    upstream = await _native_upstream(
        request,
        settings,
        method="POST",
        path="/dashboard/pipeline",
        form={"action": payload.action},
    )
    state = _state(request, settings)
    if upstream is None:
        return _native_unavailable(state)
    pipeline_state = "paused" if payload.action == "pause" else "running"
    record_activity(
        db,
        action=f"scraper_pipeline_{payload.action}d",
        target_type="scraper_pipeline",
        target_id="primary",
        actor_id=principal.user_id,
        reason=payload.reason,
        details={"pipelineState": pipeline_state},
    )
    db.commit()
    return {"ok": True, "pipelineState": pipeline_state}


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


def _state(request: Request, settings: Settings) -> ScraperProxyState:
    key = (
        settings.scraper_ops_url,
        settings.scraper_ops_user,
        settings.scraper_ops_timeout_seconds,
    )
    current = getattr(request.app.state, "scraper_proxy_state", None)
    if current is None or current.settings_key != key:
        current = ScraperProxyState(settings_key=key)
        request.app.state.scraper_proxy_state = current
    return current


def _upstream_path(path: str) -> str:
    normalized = path.strip("/")
    return f"/{normalized}" if normalized else "/dashboard"


def _mounted_path(path: str) -> str:
    if not path.startswith("/") or path.startswith("//"):
        return path
    return f"{MOUNT_PREFIX}{path}"


def _rewrite_html(
    content: bytes,
    csrf: str,
    admin_url: str,
) -> bytes:
    text = content.decode("utf-8", "replace")
    text = PATH_ATTRIBUTE.sub(
        lambda match: (
            f"{match.group('attribute')}={match.group('quote')}"
            f"{MOUNT_PREFIX}/{match.group('path')}"
        ),
        text,
    )
    headers = html.escape(
        f'{{"X-Scraper-CSRF":"{csrf}"}}',
        quote=True,
    )
    text = re.sub(
        r"<body(?P<attributes>[^>]*)>",
        (
            rf'<body\g<attributes> hx-headers="{headers}">'
            f'<a href="{html.escape(admin_url, quote=True)}" '
            'id="jawnix-admin-return">Jawnix Admin</a>'
        ),
        text,
        count=1,
        flags=re.IGNORECASE,
    )
    return text.encode("utf-8")


def _rewrite_css(content: bytes) -> bytes:
    return content.replace(b"url(/", f"url({MOUNT_PREFIX}/".encode())


def _unavailable(
    request: Request,
    state: ScraperProxyState,
    settings: Settings,
) -> HTMLResponse:
    last_success = (
        state.last_success.astimezone(timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )
        if state.last_success
        else "Never"
    )
    retry = html.escape(request.url.path, quote=True)
    admin_url = html.escape(
        f"{settings.public_base_url.rstrip('/')}/admin.html",
        quote=True,
    )
    return HTMLResponse(
        status_code=503,
        content=f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Scraper Operations unavailable</title>
<style>
body{{font-family:system-ui,sans-serif;background:#f5f7fb;color:#13213a;padding:3rem}}
main{{max-width:42rem;margin:auto;background:white;border:1px solid #d9dfeb;
border-radius:12px;padding:2rem;box-shadow:0 10px 30px #14213d14}}
.actions{{display:flex;gap:.75rem;margin-top:1.5rem}}a{{color:#175cd3}}
.button{{border:1px solid #b8c2d6;border-radius:8px;padding:.65rem 1rem;
text-decoration:none;font-weight:700}}.primary{{background:#13213a;color:white}}
</style></head><body><main>
<p>JAWNIX / ADMIN</p><h1>Scraper Operations unavailable</h1>
<p>The private Scraper connection did not respond. Jawnix feedback,
analytics, and batch administration are still available.</p>
<p><strong>Last successful connection:</strong> {last_success}</p>
<div class="actions"><a class="button primary" href="{retry}">Retry</a>
<a class="button" href="{admin_url}">Back to Jawnix Admin</a></div>
</main></body></html>""",
    )


async def forward_scraper_request(
    request: Request,
    path: str,
    principal: Principal,
    settings: Settings,
) -> Response:
    state = _state(request, settings)
    if (
        not settings.scraper_ops_url
        or not settings.scraper_ops_user
        or not settings.scraper_ops_password
    ):
        return _unavailable(request, state, settings)

    headers = {
        name: value
        for name, value in request.headers.items()
        if name.lower() in FORWARDED_REQUEST_HEADERS
    }
    headers["X-Forwarded-Prefix"] = MOUNT_PREFIX
    transport = getattr(
        request.app.state,
        "scraper_proxy_transport",
        None,
    )
    client = httpx.AsyncClient(
        base_url=settings.scraper_ops_url.rstrip("/"),
        auth=httpx.BasicAuth(
            settings.scraper_ops_user,
            settings.scraper_ops_password,
        ),
        timeout=settings.scraper_ops_timeout_seconds,
        follow_redirects=False,
        transport=transport,
    )
    try:
        upstream_request = client.build_request(
            request.method,
            _upstream_path(path),
            params=request.query_params.multi_items(),
            headers=headers,
            content=await request.body(),
        )
        upstream = await client.send(upstream_request, stream=True)
    except httpx.RequestError:
        await client.aclose()
        return _unavailable(request, state, settings)

    if upstream.status_code in {401, 403} or upstream.status_code >= 500:
        await upstream.aclose()
        await client.aclose()
        return _unavailable(request, state, settings)
    state.last_success = datetime.now(timezone.utc)

    response_headers = {
        name: value
        for name, value in upstream.headers.items()
        if name.lower() in FORWARDED_RESPONSE_HEADERS
    }
    for redirect_header in ("location", "hx-location", "hx-redirect"):
        value = upstream.headers.get(redirect_header)
        if value:
            response_headers[redirect_header] = _mounted_path(value)

    content_type = upstream.headers.get("content-type", "").lower()
    rewritten = "text/html" in content_type or "text/css" in content_type
    if rewritten:
        try:
            content = await upstream.aread()
        except httpx.RequestError:
            await upstream.aclose()
            await client.aclose()
            return _unavailable(request, state, settings)
        await client.aclose()

    if "text/html" in content_type:
        content = _rewrite_html(
            content,
            principal.csrf,
            f"{settings.public_base_url.rstrip('/')}/admin.html",
        )
        response_headers["Content-Security-Policy"] = (
            "default-src 'self' data:; script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; connect-src 'self'; "
            "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
        )
    elif "text/css" in content_type:
        content = _rewrite_css(content)
    else:
        async def stream_body():
            try:
                async for chunk in upstream.aiter_bytes():
                    yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()

        return StreamingResponse(
            stream_body(),
            status_code=upstream.status_code,
            headers=response_headers,
        )

    return Response(
        content=content,
        status_code=upstream.status_code,
        headers=response_headers,
    )
