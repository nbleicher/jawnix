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
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from jawnix.api import app
from jawnix.auth import CSRF_COOKIE
from jawnix.config import Settings, get_settings
from jawnix.database import get_db


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
