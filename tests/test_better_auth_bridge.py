"""Actual Better Auth HTTP sessions must survive the Jawnix MFA boundary."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import struct
import subprocess
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from jawnix.api import app
from jawnix.auth import CSRF_COOKIE
from jawnix.config import Settings, get_settings
from jawnix.database import get_db
from jawnix.models import Customer, CustomerProfile, ManagedAdmin, UserAccount


@pytest.fixture
def better_auth_server():
    if not os.environ.get("JAWNIX_AUTH_TEST_DATABASE_URL"):
        pytest.skip("The dedicated auth integration job supplies isolated Postgres.")
    root = Path(__file__).resolve().parents[1] / "auth-service"
    process = subprocess.Popen(
        ["node", "node_modules/tsx/dist/cli.mjs", "tests/server-fixture.ts"],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        for line in process.stdout:
            try:
                fixture = json.loads(line)
            except ValueError:
                continue
            if isinstance(fixture, dict) and "url" in fixture:
                yield fixture
                break
        else:
            raise RuntimeError("Better Auth fixture failed to start.")
    finally:
        process.terminate()
        process.wait(timeout=15)


def totp(secret):
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(
        key, struct.pack(">Q", int(time.time()) // 30), hashlib.sha1
    ).digest()
    offset = digest[-1] & 15
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{value % 1000000:06d}"


def test_provider_enrollment_step_up_and_password_reset_revoke_app_sessions(
    session,
    settings,
    better_auth_server,
):
    fixture = better_auth_server
    config = Settings(
        JAWNIX_AUTH_PROVIDER="better-auth",
        JAWNIX_BETTER_AUTH_URL=fixture["url"],
        JAWNIX_AUTH_BRIDGE_SECRET=fixture["bridgeSecret"],
        JAWNIX_COOKIE_SECURE=False,
        JAWNIX_SESSION_SECRET=settings.session_secret,
        JAWNIX_BATCH_DIR=settings.batch_dir,
    )
    app.dependency_overrides[get_settings] = lambda: config

    def database():
        yield session

    app.dependency_overrides[get_db] = database
    try:
        with httpx.Client(
            base_url=fixture["url"], headers={"Origin": fixture["url"]}
        ) as provider:
            login = provider.post(
                "/auth/sign-in/email",
                json={
                    "email": fixture["admin"]["email"],
                    "password": fixture["password"],
                },
            )
            assert login.status_code == 200
            token = login.json()["token"]
            with TestClient(app) as client:
                exchange = client.post(
                    "/api/auth/session", json={"access_token": token}
                )
                assert exchange.status_code == 200
                assert exchange.json()["next"].endswith("/mfa/enroll")
                assert client.get("/api/auth/admin-mfa/access").status_code == 401
                enrollment = client.post(
                    "/api/auth/admin-mfa/enrollment",
                    headers={"X-CSRF-Token": client.cookies[CSRF_COOKIE]},
                    json={
                        "access_token": token,
                        "password": fixture["password"],
                        "slot": "primary",
                    },
                )
                assert enrollment.status_code == 200
                assert len(enrollment.json()["backupCodes"]) > 0
                verified = client.post(
                    "/api/auth/admin-mfa/enrollment/verify",
                    headers={"X-CSRF-Token": client.cookies[CSRF_COOKIE]},
                    json={
                        "access_token": token,
                        "code": totp(enrollment.json()["manualKey"]),
                    },
                )
                assert verified.status_code == 200
                assert verified.json()["complete"] is True
                token = verified.json()["session"]["accessToken"]
                assert client.get("/api/auth/admin-mfa/access").status_code == 200
                assert client.get("/api/auth/admin-mfa").json()["enforced"] is True
                assert len(client.get("/api/auth/admin-mfa").json()["factors"]) == 1
                elevated = client.post(
                    "/api/admin/scraper/step-up",
                    headers={"X-CSRF-Token": client.cookies[CSRF_COOKIE]},
                    json={
                        "access_token": token,
                        "factor_id": enrollment.json()["factorId"],
                        "code": enrollment.json()["backupCodes"][0],
                        "method": "recovery",
                    },
                )
                assert elevated.status_code == 200
                reused = client.post(
                    "/api/admin/scraper/step-up",
                    headers={"X-CSRF-Token": client.cookies[CSRF_COOKIE]},
                    json={
                        "access_token": token,
                        "factor_id": enrollment.json()["factorId"],
                        "code": enrollment.json()["backupCodes"][0],
                        "method": "recovery",
                    },
                )
                assert reused.status_code == 422
                replacement = client.post(
                    "/api/auth/admin-mfa/replacement",
                    headers={"X-CSRF-Token": client.cookies[CSRF_COOKIE]},
                    json={
                        "access_token": token,
                        "lost_factor_id": enrollment.json()["factorId"],
                        "password": fixture["password"],
                    },
                )
                assert replacement.status_code == 200
                token = replacement.json()["session"]["accessToken"]
                pending = client.get("/api/auth/admin-mfa")
                assert pending.status_code == 200
                assert pending.json()["enforced"] is False
                assert client.get("/api/auth/admin-mfa/access").status_code == 401
                reverified = client.post(
                    "/api/auth/admin-mfa/enrollment/verify",
                    headers={"X-CSRF-Token": client.cookies[CSRF_COOKIE]},
                    json={
                        "access_token": token,
                        "code": totp(replacement.json()["manualKey"]),
                    },
                )
                assert reverified.status_code == 200
                assert client.get("/api/auth/admin-mfa/access").status_code == 200
                reset = provider.post(
                    "/auth/request-password-reset",
                    json={"email": fixture["admin"]["email"]},
                )
                assert reset.status_code == 200
                link = provider.get("/fixture/password-links").json()[-1]["url"]
                reset_token = urlparse(link).path.split("/")[-1]
                saved = provider.post(
                    "/auth/reset-password",
                    json={
                        "token": reset_token,
                        "newPassword": "replacement-password-for-tests-only",
                    },
                )
                assert saved.status_code == 200
                assert client.get("/api/auth/admin-mfa/access").status_code == 401
                assert client.get("/api/auth/admin-mfa").status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_public_verification_creates_customer_and_password_only_manager_stays_revocable(session, settings, better_auth_server):
    fixture = better_auth_server
    config = Settings(JAWNIX_AUTH_PROVIDER="better-auth", JAWNIX_BETTER_AUTH_URL=fixture["url"],
                      JAWNIX_AUTH_BRIDGE_SECRET=fixture["bridgeSecret"], JAWNIX_COOKIE_SECURE=False,
                      JAWNIX_SESSION_SECRET=settings.session_secret, JAWNIX_BATCH_DIR=settings.batch_dir)
    app.dependency_overrides[get_settings] = lambda: config
    def database():
        yield session
    app.dependency_overrides[get_db] = database
    email = f"{uuid.uuid4()}@example.test"
    try:
        with httpx.Client(base_url=fixture["url"], headers={"Origin":fixture["url"]}) as provider, TestClient(app) as client:
            created = provider.post("/auth/sign-up/email", json={"email":email, "password":fixture["password"], "name":"New Customer", "signupProfile":{
                "first_name":"New", "last_name":"Customer", "phone":"6155550100"},
                "callbackURL":fixture["url"] + "/app/complete-signup", "jawnixRole":"admin"})
            assert created.status_code == 200, created.text
            user_id = uuid.UUID(created.json()["user"]["id"])
            assert created.json()["token"] is None
            assert provider.post("/auth/sign-in/email", json={"email":email,"password":fixture["password"]}).status_code == 403
            link = next(row["url"] for row in provider.get("/fixture/verification-links").json() if row["email"] == email)
            assert provider.get(link).status_code == 302
            # Email verification signs in automatically with the real provider
            # cookie used by /app/complete-signup, without another password prompt.
            verified_session = provider.get("/auth/get-session").json()
            token = verified_session["session"]["token"]
            for _ in range(2):
                exchange = client.post("/api/auth/session", json={"access_token":token})
                assert exchange.status_code == 200, exchange.text
                assert exchange.json()["next"] == "/app/appointed-states"
            assert session.get(UserAccount, user_id) is None
            assert client.get("/api/me/overview").status_code == 409
            client.headers["X-CSRF-Token"] = client.cookies.get("jawnix_csrf")
            selected = client.post("/api/me/registration/states", json={"states":["TN", "SC"]})
            assert selected.status_code == 200 and selected.json()["next"] == "/app/overview"
            assert client.post("/api/auth/session", json={"access_token":token}).json()["next"] == "/app/overview"
            profile = session.get(CustomerProfile, user_id)
            assert profile.phone == "6155550100" and profile.licensed_states == ["SC", "TN"]
            assert session.get(Customer, profile.customer_id).active
            assert session.get(UserAccount, user_id).active
            assert client.get("/api/me/overview").status_code == 200
            assert client.get("/api/auth/admin-mfa").status_code == 403
            bridge_headers = {"Authorization":f"Bearer {fixture['bridgeSecret']}"}
            manager_email = f"{uuid.uuid4()}@example.test"
            manager = provider.post("/bridge", headers=bridge_headers, json={"action":"provision-manager", "email":manager_email, "name":"Alex fixture"}).json()
            manager_id = uuid.UUID(manager["id"])
            session.add(ManagedAdmin(auth_user_id=manager_id, name="Alex fixture", email=manager_email)); session.commit()
            setup = provider.post("/bridge", headers=bridge_headers, json={"action":"manager-access-link", "userId":str(manager_id)}).json()
            reset_token = urlparse(setup["url"]).path.split("/")[-1]
            assert provider.post("/auth/reset-password", json={"token":reset_token,"newPassword":fixture["password"]}).status_code == 200
            login = provider.post("/auth/sign-in/email", json={"email":manager_email,"password":fixture["password"]})
            assert login.status_code == 200 and "token" in login.json()
            exchange = client.post("/api/auth/session", json={"access_token":login.json()["token"]})
            assert exchange.status_code == 200 and exchange.json()["next"] == "/app/manager/uploads"
            assert exchange.json()["assurance"] == "aal1"
            assert client.get("/api/manager/access").status_code == 200
            assert client.get("/api/auth/admin-mfa").status_code == 403
            assert client.get("/api/admin/registrations").status_code == 403
            assert provider.post("/bridge", headers=bridge_headers, json={"action":"logout", "accessToken":login.json()["token"]}).status_code == 200
            assert client.get("/api/manager/access").status_code == 401
    finally:
        app.dependency_overrides.clear()
