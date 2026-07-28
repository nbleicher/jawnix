from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from jawnix.api import app
from jawnix.auth import CSRF_COOKIE, SESSION_COOKIE, _serializer
from jawnix.config import Settings, get_settings
from jawnix.database import get_db
from jawnix.mfa_provider import ProviderFactor, ProviderSession
from jawnix.models import AdminMFAState, AuditEntry


ADMIN_ID = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
PRIMARY_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
BACKUP_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")


def verified_factor(factor_id: uuid.UUID, name: str) -> ProviderFactor:
    now = datetime.now(timezone.utc)
    return ProviderFactor(
        id=factor_id,
        status="verified",
        factor_type="totp",
        friendly_name=name,
        created_at=now,
        updated_at=now,
        last_challenged_at=None,
    )


class ScraperMFAProvider:
    def __init__(self) -> None:
        self.factors = [
            verified_factor(PRIMARY_ID, "Jawnix primary"),
            verified_factor(BACKUP_ID, "Jawnix backup"),
        ]
        self.verify_calls = 0

    async def user_for_token(self, _access_token: str):
        return (
            {
                "id": str(ADMIN_ID),
                "email": "admin@example.com",
                "app_metadata": {"jawnix_role": "admin"},
            },
            {"sub": str(ADMIN_ID), "aal": "aal2"},
        )

    async def list_factors(self, _user_id: uuid.UUID):
        return list(self.factors)

    async def challenge(self, _access_token: str, _factor_id: uuid.UUID):
        return uuid.uuid4()

    async def verify(
        self,
        _access_token: str,
        _factor_id: uuid.UUID,
        _challenge_id: uuid.UUID,
        _code: str,
    ):
        self.verify_calls += 1
        return ProviderSession(
            access_token="provider-aal2-access-token-long-enough",
            refresh_token="provider-refresh-token-long-enough",
            expires_in=3600,
        )


@pytest.fixture
def workspace_settings(settings) -> Settings:
    return Settings(
        JAWNIX_BATCH_DIR=settings.batch_dir,
        JAWNIX_COOKIE_SECURE=False,
        JAWNIX_SESSION_SECRET=settings.session_secret,
        JAWNIX_ENABLE_NEW_UI=True,
        JAWNIX_SCRAPER_OPS_URL="http://10.77.0.2:8090",
        JAWNIX_SCRAPER_OPS_USER="scraper-admin",
        JAWNIX_SCRAPER_OPS_PASSWORD="upstream-secret",
        JAWNIX_SCRAPER_OPS_TIMEOUT_SECONDS=1,
    )


@pytest.fixture
def workspace_client(
    session,
    workspace_settings: Settings,
    monkeypatch,
):
    provider = ScraperMFAProvider()
    state = AdminMFAState(user_id=ADMIN_ID, session_generation=1)
    session.add(state)
    session.commit()
    csrf = "scraper-workspace-csrf"
    token = _serializer(workspace_settings).dumps(
        {
            "sub": str(ADMIN_ID),
            "email": "admin@example.com",
            "role": "admin",
            "aud": "admin",
            "aal": "aal2",
            "generation": 1,
            "factor_id": str(PRIMARY_ID),
            "csrf": csrf,
        }
    )

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[get_settings] = lambda: workspace_settings
    monkeypatch.setattr(
        "jawnix.auth.get_mfa_provider",
        lambda _settings: provider,
    )
    monkeypatch.setattr(
        "jawnix.scraper_proxy.get_mfa_provider",
        lambda _settings: provider,
    )
    now = [datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)]
    app.state.scraper_workspace_clock = lambda: now[0]
    app.state.scraper_proxy_transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers={"Content-Type": "text/html"},
            text="<html><body>GMS/OPS ready</body></html>",
        )
    )
    for attribute in ("scraper_proxy_state",):
        if hasattr(app.state, attribute):
            delattr(app.state, attribute)

    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, token)
    client.cookies.set(CSRF_COOKIE, csrf)
    try:
        yield client, csrf, provider, now
    finally:
        client.close()
        app.dependency_overrides.clear()
        for attribute in (
            "scraper_workspace_clock",
            "scraper_proxy_transport",
            "scraper_proxy_state",
        ):
            if hasattr(app.state, attribute):
                delattr(app.state, attribute)


def enter_and_verify(client: TestClient, csrf: str) -> None:
    entry = client.post(
        "/api/admin/scraper/entry",
        headers={"X-CSRF-Token": csrf},
    )
    assert entry.status_code == 200
    assert [item["id"] for item in entry.json()["factors"]] == [
        str(PRIMARY_ID),
        str(BACKUP_ID),
    ]
    verified = client.post(
        "/api/admin/scraper/step-up",
        headers={"X-CSRF-Token": csrf},
        json={
            "access_token": "provider-access-token-long-enough",
            "factor_id": str(BACKUP_ID),
            "code": "123456",
        },
    )
    assert verified.status_code == 200
    assert verified.json()["idleExpiresIn"] == 900


def test_general_aal2_is_not_a_scraper_privileged_session(workspace_client):
    client, _, provider, _ = workspace_client

    response = client.get("/api/admin/scraper/workspace")

    assert response.status_code == 401
    assert response.json() == {
        "detail": "A fresh Scraper authenticator challenge is required."
    }
    assert provider.verify_calls == 0


def test_step_up_validation_never_echoes_credentials(workspace_client):
    client, csrf, _, _ = workspace_client
    access_token = "KNOWN-SCRAPER-BEARER-TOKEN"
    code = "KNOWN-SCRAPER-CODE"

    response = client.post(
        "/api/admin/scraper/step-up",
        headers={"X-CSRF-Token": csrf},
        json={
            "access_token": access_token,
            "factor_id": str(PRIMARY_ID),
            "code": code,
        },
    )

    assert response.status_code == 422
    assert response.json() == {
        "detail": "The administrator verification request was invalid."
    }
    assert access_token not in response.text
    assert code not in response.text


def test_every_entry_invalidates_an_existing_scraper_grant(workspace_client):
    client, csrf, provider, _ = workspace_client
    enter_and_verify(client, csrf)
    assert client.get("/api/admin/scraper/workspace").status_code == 200

    reentry = client.post(
        "/api/admin/scraper/entry",
        headers={"X-CSRF-Token": csrf},
    )

    assert reentry.status_code == 200
    assert client.get("/api/admin/scraper/workspace").status_code == 401
    assert provider.verify_calls == 1


def test_scraper_grant_expires_after_fifteen_idle_minutes_and_rolls_on_activity(
    workspace_client,
):
    client, csrf, _, now = workspace_client
    enter_and_verify(client, csrf)

    now[0] += timedelta(seconds=899)
    assert client.get("/api/admin/scraper/workspace").status_code == 200
    now[0] += timedelta(seconds=899)
    assert client.get("/api/admin/scraper/workspace").status_code == 200
    now[0] += timedelta(seconds=901)

    expired = client.get("/api/admin/scraper/workspace")
    assert expired.status_code == 401
    assert expired.json()["detail"] == "Scraper privileged session expired."


def test_native_adapter_injects_credentials_normalizes_and_audits_operation(
    workspace_client,
    session,
):
    client, csrf, _, _ = workspace_client
    calls: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, text="<section>paused</section>")

    app.state.scraper_proxy_transport = httpx.MockTransport(upstream)
    enter_and_verify(client, csrf)

    operation = client.post(
        "/api/admin/scraper/pipeline",
        headers={"X-CSRF-Token": csrf},
        json={"action": "pause", "reason": "Investigating source quality"},
    )

    assert operation.status_code == 200
    assert operation.json() == {"ok": True, "pipelineState": "paused"}
    assert calls[-1].url.path == "/dashboard/pipeline"
    assert calls[-1].content == b"action=pause"
    assert calls[-1].headers["authorization"].startswith("Basic ")
    assert "upstream-secret" not in operation.text
    entries = session.scalars(
        select(AuditEntry).where(
            AuditEntry.action == "scraper_pipeline_paused"
        )
    ).all()
    assert len(entries) == 1
    assert entries[0].reason == "Investigating source quality"
    assert "upstream-secret" not in str(entries[0].details)


def test_native_mutation_requires_jawnix_csrf_before_upstream(
    workspace_client,
):
    client, csrf, _, _ = workspace_client
    calls: list[httpx.Request] = []
    app.state.scraper_proxy_transport = httpx.MockTransport(
        lambda request: (
            calls.append(request)
            or httpx.Response(200, text="<section>paused</section>")
        )
    )
    enter_and_verify(client, csrf)

    blocked = client.post(
        "/api/admin/scraper/pipeline",
        json={"action": "pause", "reason": "Investigating source quality"},
    )

    assert blocked.status_code == 403
    assert calls == []


def test_native_workspace_fails_closed_with_safe_last_success(workspace_client):
    client, csrf, _, _ = workspace_client
    unavailable = False

    def upstream(_request: httpx.Request) -> httpx.Response:
        if unavailable:
            raise httpx.ConnectTimeout("private topology 10.77.0.2 failed")
        return httpx.Response(200, text="<html><body>ready</body></html>")

    app.state.scraper_proxy_transport = httpx.MockTransport(upstream)
    enter_and_verify(client, csrf)
    first = client.get("/api/admin/scraper/workspace")
    assert first.status_code == 200
    last_success = first.json()["lastSuccessfulAt"]
    unavailable = True

    failed = client.get("/api/admin/scraper/workspace")

    assert failed.status_code == 503
    assert failed.json() == {
        "detail": "Scraper Operations is unavailable.",
        "lastSuccessfulAt": last_success,
    }
    assert "10.77.0.2" not in failed.text
    assert "controls" not in failed.text
