from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import Response
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import select

from jawnix.api import app
from jawnix.auth import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    Principal,
    _serializer,
)
from jawnix.config import Settings, get_settings
from jawnix.database import get_db
from jawnix.break_glass import BreakGlassRequest, perform_break_glass
from jawnix.mfa_provider import (
    EnrolledFactor,
    MFAProviderError,
    ProviderFactor,
    ProviderSession,
)
from jawnix.models import AdminMFAState, AuditEntry


ADMIN_ID = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


def factor(
    name: str,
    *,
    status: str = "verified",
    factor_id: uuid.UUID | None = None,
) -> ProviderFactor:
    now = datetime.now(timezone.utc)
    return ProviderFactor(
        id=factor_id or uuid.uuid4(),
        status=status,
        factor_type="totp",
        friendly_name=name,
        created_at=now,
        updated_at=now,
        last_challenged_at=None,
    )


class FakeProvider:
    def __init__(self, factors: list[ProviderFactor] | None = None):
        self.factors = list(factors or [])
        self.deleted: list[uuid.UUID] = []
        self.logout_calls = 0
        self.invalid_codes: set[str] = set()
        self.enrollment_counter = 0

    async def user_for_token(self, access_token: str):
        if access_token == "invalid-access-token-long-enough":
            raise MFAProviderError()
        assurance = "aal2" if "aal2" in access_token else "aal1"
        return (
            {
                "id": str(ADMIN_ID),
                "email": "admin@example.com",
                "app_metadata": {"jawnix_role": "admin"},
            },
            {"sub": str(ADMIN_ID), "aal": assurance},
        )

    async def admin_user(self, _user_id: uuid.UUID):
        return {
            "id": str(ADMIN_ID),
            "email": "admin@example.com",
            "app_metadata": {"jawnix_role": "admin"},
        }

    async def list_factors(self, _user_id: uuid.UUID):
        return list(self.factors)

    async def enroll(self, _access_token: str, friendly_name: str):
        self.enrollment_counter += 1
        value = factor(
            friendly_name,
            status="unverified",
            factor_id=uuid.UUID(
                f"00000000-0000-4000-8000-{self.enrollment_counter:012d}"
            ),
        )
        self.factors.append(value)
        return EnrolledFactor(
            id=value.id,
            friendly_name=friendly_name,
            qr_code="<svg>KNOWN-SECRET</svg>",
            secret="KNOWN-SECRET",
            uri="otpauth://totp/Jawnix?secret=KNOWN-SECRET",
        )

    async def challenge(
        self,
        _access_token: str,
        _factor_id: uuid.UUID,
    ):
        return uuid.uuid4()

    async def verify(
        self,
        _access_token: str,
        factor_id: uuid.UUID,
        _challenge_id: uuid.UUID,
        code: str,
    ):
        if code in self.invalid_codes:
            raise MFAProviderError(invalid_code=True)
        self.factors = [
            ProviderFactor(
                id=value.id,
                status="verified" if value.id == factor_id else value.status,
                factor_type=value.factor_type,
                friendly_name=value.friendly_name,
                created_at=value.created_at,
                updated_at=value.updated_at,
                last_challenged_at=(
                    datetime.now(timezone.utc)
                    if value.id == factor_id
                    else value.last_challenged_at
                ),
            )
            for value in self.factors
        ]
        return ProviderSession(
            access_token="provider-aal2-access-token-long-enough",
            refresh_token="provider-refresh-token-long-enough",
            expires_in=3600,
        )

    async def delete_factor(
        self,
        _user_id: uuid.UUID,
        factor_id: uuid.UUID,
    ):
        self.deleted.append(factor_id)
        self.factors = [value for value in self.factors if value.id != factor_id]

    async def logout(self, _access_token: str):
        self.logout_calls += 1


@pytest.fixture
def mfa_settings(settings):
    return Settings(
        JAWNIX_BATCH_DIR=settings.batch_dir,
        JAWNIX_COOKIE_SECURE=False,
        JAWNIX_SESSION_SECRET=settings.session_secret,
        JAWNIX_ENABLE_NEW_UI=True,
        JAWNIX_SUPABASE_URL="https://project.supabase.co",
        JAWNIX_SUPABASE_ANON_KEY="anon-key",
        SUPABASE_SERVICE_ROLE_KEY="service-role",
    )


def session_client(
    session,
    settings: Settings,
    provider: FakeProvider,
    monkeypatch,
    *,
    role: str = "admin",
    audience: str | None = None,
    assurance: str = "aal1",
    generation: int = 1,
    used_factor_id: uuid.UUID | None = None,
) -> tuple[TestClient, str]:
    csrf = "test-admin-mfa-csrf"
    token = _serializer(settings).dumps(
        {
            "sub": str(ADMIN_ID),
            "email": "admin@example.com",
            "role": role,
            "aud": audience or ("admin" if role == "admin" else "customer"),
            "aal": assurance,
            "generation": generation,
            "factor_id": str(used_factor_id) if used_factor_id else None,
            "csrf": csrf,
        }
    )

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[get_settings] = lambda: settings
    monkeypatch.setattr(
        "jawnix.auth.get_mfa_provider",
        lambda _settings: provider,
    )
    monkeypatch.setattr(
        "jawnix.admin_mfa_api.get_mfa_provider",
        lambda _settings: provider,
    )
    monkeypatch.setattr(
        "jawnix.api.get_mfa_provider",
        lambda _settings: provider,
    )
    client = TestClient(app)
    client.cookies.set(
        SESSION_COOKIE,
        token,
        domain="testserver.local",
        path="/",
    )
    client.cookies.set(
        CSRF_COOKIE,
        csrf,
        domain="testserver.local",
        path="/",
    )
    return client, csrf


def create_state(session, generation: int = 1) -> AdminMFAState:
    value = AdminMFAState(
        user_id=ADMIN_ID,
        session_generation=generation,
    )
    session.add(value)
    session.commit()
    return value


def test_require_admin_enforces_live_aal_factor_and_generation(
    session,
    mfa_settings,
    monkeypatch,
):
    primary = factor("Jawnix primary")
    backup = factor("Jawnix backup")
    provider = FakeProvider([primary, backup])
    create_state(session, generation=4)

    client, _ = session_client(
        session,
        mfa_settings,
        provider,
        monkeypatch,
        assurance="aal1",
        generation=4,
    )
    try:
        response = client.get("/api/admin/nightly-reviews")
        assert response.status_code == 401
        assert response.headers["x-jawnix-auth-next"].endswith(
            "/admin/mfa/challenge"
        )

        challenged, _ = session_client(
            session,
            mfa_settings,
            provider,
            monkeypatch,
            assurance="aal2",
            generation=4,
            used_factor_id=primary.id,
        )
        assert challenged.get("/api/admin/nightly-reviews").status_code == 200

        provider.factors = [backup]
        revoked = challenged.get("/api/admin/nightly-reviews")
        assert revoked.status_code == 401
        assert "enrollment" in revoked.json()["detail"].lower()

        provider.factors = [primary, backup]
        stale, _ = session_client(
            session,
            mfa_settings,
            provider,
            monkeypatch,
            assurance="aal2",
            generation=3,
            used_factor_id=primary.id,
        )
        assert stale.get("/api/admin/nightly-reviews").status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_customer_audience_is_forbidden_before_provider_lookup(
    session,
    mfa_settings,
    monkeypatch,
):
    provider = FakeProvider(
        [factor("Jawnix primary"), factor("Jawnix backup")]
    )
    create_state(session)
    client, _ = session_client(
        session,
        mfa_settings,
        provider,
        monkeypatch,
        role="customer",
        audience="customer",
    )
    try:
        response = client.get("/api/admin/nightly-reviews")
        assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()


def _concrete_path(path: str) -> str:
    def replace(match: re.Match[str]) -> str:
        parameter = match.group(1)
        if parameter == "path":
            return "health"
        if parameter.endswith("_id"):
            return "11111111-1111-4111-8111-111111111111"
        return "1"

    return re.sub(r"{([^}:]+)(?::[^}]+)?}", replace, path)


def test_every_administrator_route_observably_passes_through_the_mfa_gate(
    session,
    mfa_settings,
    monkeypatch,
):
    """A newly added administrator route fails this test if it skips the gate."""

    provider = FakeProvider(
        [factor("Jawnix primary"), factor("Jawnix backup")]
    )
    create_state(session)
    client, csrf = session_client(
        session,
        mfa_settings,
        provider,
        monkeypatch,
        assurance="aal1",
    )
    excluded = {"/admin/scraper/session", "/admin/scraper/logout"}
    routes = [
        route
        for route in app.routes
        if isinstance(route, APIRoute)
        and (
            route.path.startswith("/api/admin")
            or route.path.startswith("/admin/scraper")
        )
        and route.path not in excluded
    ]
    # Inventory guard: accidentally narrowing the route selection must not turn
    # this into a green test over a token sample.
    assert len(routes) >= 39
    failures: list[str] = []
    try:
        for route in routes:
            path = _concrete_path(route.path)
            for method in sorted(
                route.methods & {"GET", "POST", "PUT", "PATCH", "DELETE"}
            ):
                response = client.request(
                    method,
                    path,
                    headers={"X-CSRF-Token": csrf},
                    json={} if method not in {"GET"} else None,
                )
                if response.status_code != 401:
                    failures.append(
                        f"{method} {route.path}: {response.status_code}"
                    )
        assert failures == []
    finally:
        app.dependency_overrides.clear()


def test_dual_enrollment_is_resumable_audited_and_activates_atomically(
    session,
    mfa_settings,
    monkeypatch,
):
    provider = FakeProvider()
    state = create_state(session)
    client, csrf = session_client(
        session,
        mfa_settings,
        provider,
        monkeypatch,
    )
    try:
        primary = client.post(
            "/api/auth/admin-mfa/enrollment",
            headers={"X-CSRF-Token": csrf},
            json={
                "access_token": "provider-aal1-access-token-long-enough",
                "slot": "primary",
            },
        )
        assert primary.status_code == 200
        assert primary.json()["manualKey"] == "KNOWN-SECRET"
        assert len(provider.factors) == 1
        assert provider.factors[0].status == "unverified"

        verified_primary = client.post(
            "/api/auth/admin-mfa/enrollment/verify",
            headers={"X-CSRF-Token": csrf},
            json={
                "access_token": "provider-aal1-access-token-long-enough",
                "code": "123 456",
            },
        )
        assert verified_primary.status_code == 200
        assert verified_primary.json()["complete"] is False
        assert verified_primary.json()["session"]["accessToken"].find(
            "aal2"
        ) >= 0
        assert client.get("/api/admin/nightly-reviews").status_code == 401

        backup = client.post(
            "/api/auth/admin-mfa/enrollment",
            headers={"X-CSRF-Token": client.cookies[CSRF_COOKIE]},
            json={
                "access_token": "provider-aal2-access-token-long-enough",
                "slot": "backup",
            },
        )
        assert backup.status_code == 200
        assert len(provider.factors) == 2

        verified_backup = client.post(
            "/api/auth/admin-mfa/enrollment/verify",
            headers={"X-CSRF-Token": client.cookies[CSRF_COOKIE]},
            json={
                "access_token": "provider-aal2-access-token-long-enough",
                "code": "123-456",
            },
        )
        assert verified_backup.status_code == 200
        assert verified_backup.json()["complete"] is True
        assert client.get("/api/admin/nightly-reviews").status_code == 200

        session.refresh(state)
        assert state.enrollment_stage == "complete"
        actions = [
            value.action
            for value in session.scalars(
                select(AuditEntry).order_by(AuditEntry.created_at)
            )
        ]
        assert actions == [
            "admin_mfa_enrollment_started",
            "admin_mfa_factor_verified",
            "admin_mfa_enrollment_started",
            "admin_mfa_factor_verified",
        ]
        serialized_audits = json.dumps(
            [value.details for value in session.scalars(select(AuditEntry))]
        )
        assert "KNOWN-SECRET" not in serialized_audits
        assert "123456" not in serialized_audits
    finally:
        app.dependency_overrides.clear()


def test_cancellation_restores_the_pre_enrollment_factor_set(
    session,
    mfa_settings,
    monkeypatch,
):
    provider = FakeProvider()
    state = create_state(session)
    client, csrf = session_client(
        session,
        mfa_settings,
        provider,
        monkeypatch,
    )
    try:
        client.post(
            "/api/auth/admin-mfa/enrollment",
            headers={"X-CSRF-Token": csrf},
            json={
                "access_token": "provider-aal1-access-token-long-enough",
                "slot": "primary",
            },
        )
        new_id = provider.factors[0].id
        response = client.post(
            "/api/auth/admin-mfa/enrollment/cancel",
            headers={"X-CSRF-Token": csrf},
            json={
                "access_token": "provider-aal1-access-token-long-enough"
            },
        )
        assert response.status_code == 200
        assert provider.factors == []
        assert provider.deleted == [new_id]
        session.refresh(state)
        assert state.enrollment_stage == "idle"
        assert state.active_factor_id is None
        assert state.enrollment_new_factor_ids == []
    finally:
        app.dependency_overrides.clear()


def test_challenge_failures_are_committed_and_throttled_per_account(
    session,
    mfa_settings,
    monkeypatch,
):
    primary = factor("Jawnix primary")
    provider = FakeProvider([primary, factor("Jawnix backup")])
    provider.invalid_codes.add("000000")
    state = create_state(session)
    client, csrf = session_client(
        session,
        mfa_settings,
        provider,
        monkeypatch,
    )
    try:
        responses = [
            client.post(
                "/api/auth/admin-mfa/challenge",
                headers={"X-CSRF-Token": csrf},
                json={
                    "access_token": (
                        "provider-aal1-access-token-long-enough"
                    ),
                    "factor_id": str(primary.id),
                    "code": "000000",
                },
            )
            for _ in range(mfa_settings.admin_mfa_max_attempts)
        ]
        assert [value.status_code for value in responses] == [
            422,
            422,
            422,
            422,
            429,
        ]
        session.refresh(state)
        assert state.locked_until is not None
        failures = list(
            session.scalars(
                select(AuditEntry).where(
                    AuditEntry.action == "admin_mfa_challenge_failed"
                )
            )
        )
        assert len(failures) == mfa_settings.admin_mfa_max_attempts
        assert failures[-1].details["throttled"] is True
        assert "000000" not in json.dumps(
            [value.details for value in failures]
        )
    finally:
        app.dependency_overrides.clear()


def test_all_new_mutating_mfa_endpoints_retain_csrf_protection(
    session,
    mfa_settings,
    monkeypatch,
):
    provider = FakeProvider(
        [factor("Jawnix primary"), factor("Jawnix backup")]
    )
    create_state(session)
    client, _ = session_client(
        session,
        mfa_settings,
        provider,
        monkeypatch,
    )
    payloads = {
        "/api/auth/admin-mfa/enrollment": {
            "access_token": "provider-aal1-access-token-long-enough",
            "slot": "primary",
        },
        "/api/auth/admin-mfa/enrollment/verify": {
            "access_token": "provider-aal1-access-token-long-enough",
            "code": "123456",
        },
        "/api/auth/admin-mfa/challenge": {
            "access_token": "provider-aal1-access-token-long-enough",
            "factor_id": str(provider.factors[0].id),
            "code": "123456",
        },
        "/api/auth/admin-mfa/enrollment/cancel": {
            "access_token": "provider-aal1-access-token-long-enough",
        },
        "/api/auth/admin-mfa/replacement": {
            "access_token": "provider-aal2-access-token-long-enough",
            "lost_factor_id": str(provider.factors[0].id),
        },
        "/api/auth/admin-mfa/logout-everywhere": {
            "access_token": "provider-aal1-access-token-long-enough",
        },
    }
    try:
        for path, payload in payloads.items():
            response = client.post(path, json=payload)
            assert response.status_code == 403, path
            assert response.json()["detail"] == "CSRF validation failed."
    finally:
        app.dependency_overrides.clear()


def test_mfa_validation_errors_redact_bearer_tokens_and_codes(
    session,
    mfa_settings,
    monkeypatch,
):
    provider = FakeProvider()
    create_state(session)
    client, csrf = session_client(
        session,
        mfa_settings,
        provider,
        monkeypatch,
    )
    try:
        response = client.post(
            "/api/auth/admin-mfa/enrollment/verify",
            headers={"X-CSRF-Token": csrf},
            json={
                "access_token": "KNOWN-BEARER-TOKEN",
                "code": "KNOWN-CODE",
            },
        )
        assert response.status_code == 422
        assert response.json() == {
            "detail": (
                "The administrator verification request was invalid."
            )
        }
        assert "KNOWN-BEARER-TOKEN" not in response.text
        assert "KNOWN-CODE" not in response.text
    finally:
        app.dependency_overrides.clear()


def test_expired_session_is_refused_before_mfa_state(
    session,
    mfa_settings,
):
    expired_settings = Settings(
        **{
            **mfa_settings.model_dump(),
            "JAWNIX_SESSION_TTL_SECONDS": -1,
        }
    )
    token = _serializer(expired_settings).dumps(
        {
            "sub": str(ADMIN_ID),
            "email": "admin@example.com",
            "role": "admin",
            "aud": "admin",
            "aal": "aal2",
            "generation": 1,
            "csrf": "expired",
        }
    )
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, token)
    app.dependency_overrides[get_settings] = lambda: expired_settings
    try:
        assert client.get("/api/admin/nightly-reviews").status_code == 401
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize(
    ("factors", "assurance", "expected_next"),
    [
        ([], "aal1", "/app/admin/mfa/enroll"),
        (
            [factor("Jawnix primary"), factor("Jawnix backup")],
            "aal1",
            "/app/admin/mfa/challenge",
        ),
        (
            [factor("Jawnix primary"), factor("Jawnix backup")],
            "aal2",
            "/app/admin/overview",
        ),
    ],
)
def test_admin_password_exchange_routes_to_the_required_mfa_step(
    session,
    mfa_settings,
    monkeypatch,
    factors,
    assurance,
    expected_next,
):
    provider = FakeProvider(factors)

    async def fake_verify(_token, _settings):
        return {
            "id": str(ADMIN_ID),
            "email": "admin@example.com",
            "app_metadata": {"jawnix_role": "admin"},
            "_jawnix_auth_claims": {
                "sub": str(ADMIN_ID),
                "aal": assurance,
            },
        }

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[get_settings] = lambda: mfa_settings
    monkeypatch.setattr("jawnix.api.verify_supabase_token", fake_verify)
    monkeypatch.setattr(
        "jawnix.api.get_mfa_provider",
        lambda _settings: provider,
    )
    try:
        response = TestClient(app).post(
            "/api/auth/session",
            json={
                "access_token": "provider-access-token-long-enough",
                "requested_next": "/admin.html",
            },
        )
        assert response.status_code == 200
        assert response.json()["next"] == expected_next
        assert response.json()["assurance"] == assurance
        state = session.get(AdminMFAState, ADMIN_ID)
        assert state is not None
        assert state.session_generation == 1
    finally:
        app.dependency_overrides.clear()


def test_break_glass_requires_two_people_revokes_first_and_audits(
    session,
):
    provider = FakeProvider(
        [factor("Jawnix primary"), factor("Jawnix backup")]
    )
    state = create_state(session, generation=7)
    request = BreakGlassRequest(
        target_user_id=ADMIN_ID,
        target_email="admin@example.com",
        operator="Morgan Operator",
        authorizer="Avery Authorizer",
        reason="Both authenticators were lost",
        reference="INC-49",
    )

    result = __import__("asyncio").run(
        perform_break_glass(session, provider, request)
    )

    assert result["removedFactorCount"] == 2
    assert result["accessRestoredTo"] == "mfa_enrollment_only"
    session.refresh(state)
    assert state.session_generation == 8
    assert state.enrollment_stage == "break_glass_reenrollment_required"
    audits = list(
        session.scalars(select(AuditEntry).order_by(AuditEntry.created_at))
    )
    assert [value.action for value in audits] == [
        "admin_mfa_break_glass_authorized",
        "admin_mfa_break_glass",
    ]
    assert audits[-1].actor_user_id == "Morgan Operator"
    assert audits[-1].details["authorizer"] == "Avery Authorizer"
    assert audits[-1].details["recipientEmail"] == "admin@example.com"
    assert audits[-1].reason == "Both authenticators were lost"
    assert provider.factors == []


def test_break_glass_rejects_self_authorization_before_provider_change(
    session,
):
    provider = FakeProvider([factor("Jawnix primary")])
    request = BreakGlassRequest(
        target_user_id=ADMIN_ID,
        target_email="admin@example.com",
        operator="Same Person",
        authorizer="same person",
        reason="Lost",
        reference="INC-unsafe",
    )

    with pytest.raises(ValueError, match="different people"):
        __import__("asyncio").run(
            perform_break_glass(session, provider, request)
        )

    assert len(provider.factors) == 1
    assert session.scalar(select(AuditEntry)) is None
