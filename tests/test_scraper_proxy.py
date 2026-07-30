from __future__ import annotations

import asyncio
import base64
import re
import uuid

import httpx
import pytest
from fastapi import Request
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from jawnix.api import app
from jawnix.auth import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    Principal,
    _serializer,
)
from jawnix.config import Settings, get_settings
from jawnix.scraper_proxy import (
    MOUNT_PREFIX,
    SCRAPER_SESSION_COOKIE,
    _scraper_serializer,
    forward_scraper_request,
)


def proxy_settings() -> Settings:
    return Settings(
        JAWNIX_COOKIE_SECURE=False,
        JAWNIX_SESSION_SECRET="proxy-test-secret-at-least-long-enough",
        JAWNIX_PUBLIC_BASE_URL="https://jawnix.test",
        JAWNIX_SCRAPER_OPS_URL="http://10.77.0.2:8090",
        JAWNIX_SCRAPER_OPS_ORIGIN="https://scraper.jawnix.test",
        JAWNIX_SCRAPER_OPS_USER="scraper-admin",
        JAWNIX_SCRAPER_OPS_PASSWORD="upstream-secret",
        JAWNIX_SCRAPER_OPS_TIMEOUT_SECONDS=1,
    )


def session_client(
    settings: Settings,
    role: str = "admin",
) -> tuple[TestClient, str]:
    csrf = "test-csrf-token"
    token = _serializer(settings).dumps(
        {
            "sub": str(uuid.uuid4()),
            "email": "admin@example.com",
            "role": role,
            "csrf": csrf,
        }
    )
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, token)
    client.cookies.set(CSRF_COOKIE, csrf)
    return client, csrf


def isolated_session_client(
    settings: Settings,
    role: str = "admin",
) -> tuple[TestClient, str]:
    csrf = "isolated-scraper-csrf"
    token = _scraper_serializer(settings).dumps(
        {
            "sub": str(uuid.uuid4()),
            "email": "admin@example.com",
            "role": role,
            "csrf": csrf,
        }
    )
    client = TestClient(
        app,
        base_url="https://scraper.jawnix.test",
    )
    client.cookies.set(
        SCRAPER_SESSION_COOKIE,
        token,
        path=MOUNT_PREFIX,
    )
    return client, csrf


def configure_proxy(
    settings: Settings,
    handler,
) -> None:
    app.dependency_overrides[get_settings] = lambda: settings
    app.state.scraper_proxy_transport = httpx.MockTransport(handler)
    if hasattr(app.state, "scraper_proxy_state"):
        delattr(app.state, "scraper_proxy_state")


def clear_proxy() -> None:
    app.dependency_overrides.clear()
    for attribute in ("scraper_proxy_transport", "scraper_proxy_state"):
        if hasattr(app.state, attribute):
            delattr(app.state, attribute)


def test_scraper_mount_requires_admin_session():
    settings = proxy_settings()
    configure_proxy(
        settings,
        lambda _: httpx.Response(200, text="should not be reached"),
    )
    try:
        response = TestClient(
            app,
            base_url="https://scraper.jawnix.test",
        ).get("/admin/scraper/")
        assert response.status_code == 401
    finally:
        clear_proxy()


def test_scraper_html_rewrites_navigation_assets_and_htmx_paths():
    settings = proxy_settings()
    observed = {}

    def upstream(request: httpx.Request) -> httpx.Response:
        observed["url"] = str(request.url)
        observed["authorization"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            text=(
                '<html><head><link href="/static/app.css"></head>'
                '<body><a href="/states">States</a>'
                '<section hx-get="/frag/dashboard/stats"></section>'
                '<button hx-post="/dashboard/pipeline">Pause</button></body>'
                "</html>"
            ),
        )

    configure_proxy(settings, upstream)
    client, csrf = isolated_session_client(settings)
    try:
        response = client.get("/admin/scraper/")
        assert response.status_code == 200
        assert observed["url"] == "http://10.77.0.2:8090/dashboard"
        assert observed["authorization"] == (
            "Basic "
            + base64.b64encode(
                b"scraper-admin:upstream-secret"
            ).decode()
        )
        assert 'href="/admin/scraper/static/app.css"' in response.text
        assert 'href="/admin/scraper/states"' in response.text
        assert 'hx-get="/admin/scraper/frag/dashboard/stats"' in response.text
        assert 'hx-post="/admin/scraper/dashboard/pipeline"' in response.text
        assert "X-Scraper-CSRF" in response.text
        assert csrf in response.text
        assert "test-csrf-token" not in response.text
        assert 'href="https://jawnix.test/admin.html"' in response.text
        assert "upstream-secret" not in response.text
    finally:
        client.close()
        clear_proxy()


def test_scraper_mutation_requires_jawnix_csrf_before_upstream_call():
    settings = proxy_settings()
    calls = []

    def upstream(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, text="<section>paused</section>")

    configure_proxy(settings, upstream)
    client, csrf = isolated_session_client(settings)
    try:
        blocked = client.post(
            "/admin/scraper/dashboard/pipeline",
            data={"action": "pause"},
        )
        assert blocked.status_code == 403
        assert calls == []

        allowed = client.post(
            "/admin/scraper/dashboard/pipeline",
            data={"action": "pause"},
            headers={"X-Scraper-CSRF": csrf},
        )
        assert allowed.status_code == 200
        assert len(calls) == 1
        assert calls[0].content == b"action=pause"
        assert calls[0].headers["authorization"].startswith("Basic ")
    finally:
        client.close()
        clear_proxy()


def test_scraper_outage_fails_closed_with_last_success_and_retry():
    settings = proxy_settings()
    unavailable = False

    def upstream(request: httpx.Request) -> httpx.Response:
        if unavailable:
            raise httpx.ConnectTimeout("wireguard peer unavailable")
        return httpx.Response(200, text="<html><body>ready</body></html>")

    configure_proxy(settings, upstream)
    client, _ = isolated_session_client(settings)
    try:
        first = client.get("/admin/scraper/")
        assert first.status_code == 200
        unavailable = True

        failed = client.get("/admin/scraper/states")
        assert failed.status_code == 503
        assert "Scraper Operations unavailable" in failed.text
        assert "Last successful connection" in failed.text
        assert 'href="/admin/scraper/states"' in failed.text
        assert 'href="https://jawnix.test/admin.html"' in failed.text
        assert "wireguard peer unavailable" not in failed.text
    finally:
        client.close()
        clear_proxy()


def test_scraper_redirect_stays_inside_mount():
    settings = proxy_settings()
    configure_proxy(
        settings,
        lambda _: httpx.Response(
            307,
            headers={"Location": "/dashboard"},
        ),
    )
    client, _ = isolated_session_client(settings)
    try:
        response = client.get(
            "/admin/scraper/history",
            follow_redirects=False,
        )
        assert response.status_code == 307
        assert response.headers["location"] == "/admin/scraper/dashboard"
    finally:
        client.close()
        clear_proxy()


def test_scraper_mount_rejects_customer_session():
    settings = proxy_settings()
    configure_proxy(
        settings,
        lambda _: httpx.Response(200, text="should not be reached"),
    )
    client, _ = isolated_session_client(settings, role="customer")
    try:
        response = client.get("/admin/scraper/")
        assert response.status_code == 403
    finally:
        client.close()
        clear_proxy()


def test_scraper_static_css_paths_remain_inside_mount():
    settings = proxy_settings()

    def upstream(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/static/app.css"
        return httpx.Response(
            200,
            headers={"Content-Type": "text/css"},
            content=(
                b"@font-face{src:url("
                b"/static/fonts/jetbrains-mono.woff2)}"
            ),
        )

    configure_proxy(settings, upstream)
    client, _ = isolated_session_client(settings)
    try:
        response = client.get("/admin/scraper/static/app.css")
        assert response.status_code == 200
        assert (
            "url(/admin/scraper/static/fonts/jetbrains-mono.woff2)"
            in response.text
        )
    finally:
        client.close()
        clear_proxy()


def test_primary_origin_hands_admin_off_without_contacting_upstream():
    settings = proxy_settings()
    calls = []

    def upstream(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, text="should not be reached")

    configure_proxy(settings, upstream)
    client, _ = session_client(settings)
    try:
        response = client.get("/admin/scraper/")
        assert response.status_code == 200
        assert calls == []
        assert (
            'action="https://scraper.jawnix.test/admin/scraper/session"'
            in response.text
        )
        assert "upstream-secret" not in response.text
    finally:
        client.close()
        clear_proxy()


def test_signed_handoff_creates_isolated_session_and_opens_dashboard():
    settings = proxy_settings()
    calls = []

    def upstream(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html"},
            text="<html><body>ready</body></html>",
        )

    configure_proxy(settings, upstream)
    primary, _ = session_client(settings)
    isolated = TestClient(
        app,
        base_url="https://scraper.jawnix.test",
    )
    try:
        transfer = primary.get("/admin/scraper/")
        token = re.search(
            r'name="handoff" value="([^"]+)"',
            transfer.text,
        )
        assert token is not None

        accepted = isolated.post(
            "/admin/scraper/session",
            data={"handoff": token.group(1)},
            follow_redirects=False,
        )
        assert accepted.status_code == 303
        assert accepted.headers["location"] == "/admin/scraper/"
        assert "HttpOnly" in accepted.headers["set-cookie"]

        dashboard = isolated.get("/admin/scraper/")
        assert dashboard.status_code == 200
        assert "ready" in dashboard.text
        assert len(calls) == 1
    finally:
        primary.close()
        isolated.close()
        clear_proxy()


def test_isolated_scraper_session_cannot_authenticate_native_admin_api():
    settings = proxy_settings()
    configure_proxy(
        settings,
        lambda _: httpx.Response(200, text="should not be reached"),
    )
    client, _ = isolated_session_client(settings)
    try:
        response = client.get("/api/admin/requests")
        assert response.status_code == 401
    finally:
        client.close()
        clear_proxy()


def test_scraper_logout_clears_isolated_session():
    settings = proxy_settings()
    configure_proxy(
        settings,
        lambda _: httpx.Response(200, text="ready"),
    )
    primary, _ = session_client(settings)
    client = TestClient(
        app,
        base_url="https://scraper.jawnix.test",
    )
    try:
        transfer = primary.get("/admin/scraper/")
        token = re.search(
            r'name="handoff" value="([^"]+)"',
            transfer.text,
        )
        assert token is not None
        accepted = client.post(
            "/admin/scraper/session",
            data={"handoff": token.group(1)},
            follow_redirects=False,
        )
        assert accepted.status_code == 303
        assert client.get("/admin/scraper/").status_code == 200

        logout = client.post(
            "/admin/scraper/logout",
            headers={"Origin": "https://jawnix.test"},
        )
        assert logout.status_code == 204
        assert (
            f"{SCRAPER_SESSION_COOKIE}=" in logout.headers["set-cookie"]
        )
        assert "Max-Age=0" in logout.headers["set-cookie"]

        dashboard = client.get("/admin/scraper/")
        assert dashboard.status_code == 401
    finally:
        primary.close()
        client.close()
        clear_proxy()


def test_database_download_is_streamed_without_eager_buffering():
    settings = proxy_settings()

    class DeferredStream(httpx.AsyncByteStream):
        iterated = False

        async def __aiter__(self):
            self.iterated = True
            yield b"phone,title\n"
            yield b"2155550100,Example\n"

    stream = DeferredStream()

    def upstream(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "Content-Type": "text/csv",
                "Content-Disposition": 'attachment; filename="leads.csv"',
            },
            stream=stream,
        )

    configure_proxy(settings, upstream)

    async def exercise() -> None:
        async def receive():
            return {
                "type": "http.request",
                "body": b"",
                "more_body": False,
            }

        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/admin/scraper/states/PA/download",
                "query_string": b"",
                "headers": [],
                "scheme": "https",
                "server": ("scraper.jawnix.test", 443),
                "app": app,
            },
            receive,
        )
        response = await forward_scraper_request(
            request,
            "states/PA/download",
            principal=Principal(
                user_id=uuid.uuid4(),
                email="admin@example.com",
                role="admin",
                csrf="isolated-scraper-csrf",
            ),
            settings=settings,
        )
        assert isinstance(response, StreamingResponse)
        assert stream.iterated is False
        body = b"".join([chunk async for chunk in response.body_iterator])
        assert body.startswith(b"phone,title")
        assert stream.iterated is True

    try:
        asyncio.run(exercise())
    finally:
        clear_proxy()


def test_scraper_ops_timeout_default_exceeds_the_slowest_known_page(monkeypatch):
    """The Scraper database page measured 11.3s against production on
    2026-07-29 — several live aggregates over ~772k rows. The default was 10,
    so httpx raised ReadTimeout, `_raw_native_upstream` swallowed it as a
    RequestError, and the screen reported the upstream as unresponsive for a
    request that was merely slow.

    Ceilings below a known-good response time are worse than no ceiling: they
    fail correctly-working systems and blame the wrong component. 30 keeps
    ~2.5x headroom over the slowest measured page; a regression to, say, 15
    would sit 3.7s above a measurement that already varies by more than that.
    """
    from jawnix.config import Settings

    monkeypatch.delenv("JAWNIX_SCRAPER_OPS_TIMEOUT_SECONDS", raising=False)
    settings = Settings(JAWNIX_SESSION_SECRET="test-secret-at-least-long-enough")

    assert settings.scraper_ops_timeout_seconds >= 30
