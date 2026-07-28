from __future__ import annotations

import asyncio
import base64
import json
import uuid

import httpx
import pytest

from jawnix.config import Settings
from jawnix.mfa_provider import MFAProviderError, SupabaseMFAProvider


USER_ID = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
FACTOR_ID = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
CHALLENGE_ID = uuid.UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")


def jwt(claims: dict) -> str:
    def encoded(value: dict) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{encoded({'alg': 'none'})}.{encoded(claims)}.signature"


def provider_settings() -> Settings:
    return Settings(
        JAWNIX_SUPABASE_URL="https://project.supabase.co",
        JAWNIX_SUPABASE_ANON_KEY="anon-key",
        SUPABASE_SERVICE_ROLE_KEY="service-role-key",
    )


def install_transport(monkeypatch, handler):
    original = httpx.AsyncClient

    def factory(*_args, **kwargs):
        return original(
            transport=httpx.MockTransport(handler),
            timeout=kwargs.get("timeout"),
        )

    monkeypatch.setattr("jawnix.mfa_provider.httpx.AsyncClient", factory)


def test_real_provider_adapter_uses_supabase_mfa_contract(monkeypatch):
    access_token = jwt({"sub": str(USER_ID), "aal": "aal1"})
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path.endswith("/user"):
            return httpx.Response(
                200,
                json={
                    "id": str(USER_ID),
                    "email": "admin@example.com",
                    "app_metadata": {"jawnix_role": "admin"},
                },
            )
        if path.endswith(f"/admin/users/{USER_ID}/factors"):
            return httpx.Response(
                200,
                json=[
                    {
                        "id": str(FACTOR_ID),
                        "status": "verified",
                        "factor_type": "totp",
                        "friendly_name": "Jawnix primary",
                        "created_at": "2026-07-28T12:00:00Z",
                        "updated_at": "2026-07-28T12:10:00Z",
                        "last_challenged_at": "2026-07-28T12:20:00Z",
                    }
                ],
            )
        if path.endswith("/factors"):
            return httpx.Response(
                200,
                json={
                    "id": str(FACTOR_ID),
                    "type": "totp",
                    "friendly_name": "Jawnix primary",
                    "totp": {
                        "qr_code": "<svg>KNOWN-SECRET</svg>",
                        "secret": "KNOWN-SECRET",
                        "uri": "otpauth://totp/Jawnix",
                    },
                },
            )
        if path.endswith(f"/factors/{FACTOR_ID}/challenge"):
            return httpx.Response(
                200,
                json={"id": str(CHALLENGE_ID), "type": "totp"},
            )
        if path.endswith(f"/factors/{FACTOR_ID}/verify"):
            return httpx.Response(
                200,
                json={
                    "access_token": "new-access-token",
                    "refresh_token": "new-refresh-token",
                    "expires_in": 3600,
                },
            )
        if path.endswith(
            f"/admin/users/{USER_ID}/factors/{FACTOR_ID}"
        ):
            return httpx.Response(200, json={"id": str(FACTOR_ID)})
        raise AssertionError(f"unexpected provider request: {request.method} {path}")

    install_transport(monkeypatch, handler)
    provider = SupabaseMFAProvider(provider_settings())

    async def exercise():
        user, claims = await provider.user_for_token(access_token)
        factors = await provider.list_factors(USER_ID)
        enrolled = await provider.enroll(access_token, "Jawnix primary")
        challenge_id = await provider.challenge(access_token, FACTOR_ID)
        session = await provider.verify(
            access_token,
            FACTOR_ID,
            challenge_id,
            "123456",
        )
        await provider.delete_factor(USER_ID, FACTOR_ID)
        return user, claims, factors, enrolled, session

    user, claims, factors, enrolled, session = asyncio.run(exercise())
    assert user["id"] == str(USER_ID)
    assert claims["aal"] == "aal1"
    assert factors[0].verified_totp
    assert factors[0].last_challenged_at is not None
    assert enrolled.secret == "KNOWN-SECRET"
    assert session.refresh_token == "new-refresh-token"

    by_path = {request.url.path: request for request in requests}
    user_request = by_path["/auth/v1/user"]
    assert user_request.headers["apikey"] == "anon-key"
    assert user_request.headers["authorization"] == f"Bearer {access_token}"

    admin_request = by_path[
        f"/auth/v1/admin/users/{USER_ID}/factors"
    ]
    assert admin_request.headers["apikey"] == "service-role-key"
    assert (
        admin_request.headers["authorization"]
        == "Bearer service-role-key"
    )

    enroll_body = json.loads(by_path["/auth/v1/factors"].content)
    assert enroll_body == {
        "factor_type": "totp",
        "friendly_name": "Jawnix primary",
        "issuer": "Jawnix",
    }
    verify_body = json.loads(
        by_path[f"/auth/v1/factors/{FACTOR_ID}/verify"].content
    )
    assert verify_body == {
        "challenge_id": str(CHALLENGE_ID),
        "code": "123456",
    }


def test_provider_error_body_never_escapes_the_boundary(monkeypatch):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={
                "error_code": "mfa_verification_failed",
                "message": "code 123456 for secret KNOWN-SECRET failed",
            },
        )

    install_transport(monkeypatch, handler)
    provider = SupabaseMFAProvider(provider_settings())

    with pytest.raises(MFAProviderError) as caught:
        asyncio.run(
            provider.verify(
                "provider-access-token",
                FACTOR_ID,
                CHALLENGE_ID,
                "123456",
            )
        )

    assert caught.value.invalid_code is True
    assert "123456" not in str(caught.value)
    assert "KNOWN-SECRET" not in str(caught.value)
