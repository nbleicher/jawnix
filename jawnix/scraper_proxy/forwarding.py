from __future__ import annotations

import html
import re
from datetime import datetime, timezone

import httpx
from fastapi import Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse

from ..auth import Principal
from ..config import Settings
from .core import MOUNT_PREFIX, ScraperProxyState, _state


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
