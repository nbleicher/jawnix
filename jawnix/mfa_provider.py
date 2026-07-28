"""The sole boundary between Jawnix and Supabase's MFA API.

No caller outside this module knows Supabase MFA endpoint paths or forwards a
provider error body.  In particular, TOTP secrets and submitted codes are never
included in an exception, log message, or audit entry.
"""

from __future__ import annotations

import base64
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from .config import Settings


class MFAProviderError(RuntimeError):
    """A deliberately non-revealing identity-provider failure."""

    def __init__(
        self,
        message: str = "The verification service could not complete the request.",
        *,
        invalid_code: bool = False,
    ):
        super().__init__(message)
        self.invalid_code = invalid_code


@dataclass(frozen=True)
class ProviderFactor:
    id: uuid.UUID
    status: str
    factor_type: str
    friendly_name: str
    created_at: datetime | None
    updated_at: datetime | None
    last_challenged_at: datetime | None

    @property
    def verified_totp(self) -> bool:
        return self.status == "verified" and self.factor_type == "totp"


@dataclass(frozen=True)
class EnrolledFactor:
    id: uuid.UUID
    friendly_name: str
    qr_code: str
    secret: str
    uri: str


@dataclass(frozen=True)
class ProviderSession:
    access_token: str
    refresh_token: str
    expires_in: int


def _date(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def access_token_claims(access_token: str) -> dict[str, Any]:
    """Decode claims only after the same token was accepted by Supabase.

    This helper does not verify a signature.  Its callers first send the token
    to Supabase's authenticated user endpoint and compare ``sub`` with that
    response, making these the claims from the exact provider-accepted token.
    """

    try:
        encoded = access_token.split(".")[1]
        padded = encoded + "=" * (-len(encoded) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded))
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (IndexError, ValueError, json.JSONDecodeError):
        raise MFAProviderError("The identity session is invalid or expired.") from None


class SupabaseMFAProvider:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _base_url(self) -> str:
        if not self.settings.supabase_url:
            raise MFAProviderError("The verification service is not configured.")
        return f"{self.settings.supabase_url.rstrip('/')}/auth/v1"

    async def _request(
        self,
        method: str,
        path: str,
        *,
        access_token: str | None = None,
        service_role: bool = False,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        key = (
            self.settings.supabase_service_role_key
            if service_role
            else self.settings.supabase_anon_key
        )
        bearer = key if service_role else access_token
        if not key or not bearer:
            raise MFAProviderError("The verification service is not configured.")
        headers = {
            "apikey": key,
            "Authorization": f"Bearer {bearer}",
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.request(
                    method,
                    f"{self._base_url()}{path}",
                    headers=headers,
                    json=payload,
                )
        except httpx.HTTPError:
            raise MFAProviderError() from None

        if response.status_code >= 400:
            invalid_code = False
            try:
                error_code = str(response.json().get("error_code") or "")
                invalid_code = error_code in {
                    "mfa_verification_failed",
                    "mfa_challenge_expired",
                }
            except (ValueError, AttributeError):
                pass
            raise MFAProviderError(invalid_code=invalid_code)
        if response.status_code == 204 or not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            raise MFAProviderError() from None

    async def user_for_token(
        self,
        access_token: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        user = await self._request(
            "GET",
            "/user",
            access_token=access_token,
        )
        claims = access_token_claims(access_token)
        if str(claims.get("sub") or "") != str(user.get("id") or ""):
            raise MFAProviderError("The identity session is invalid or expired.")
        return user, claims

    async def list_factors(self, user_id: uuid.UUID) -> list[ProviderFactor]:
        values = await self._request(
            "GET",
            f"/admin/users/{user_id}/factors",
            service_role=True,
        )
        return [
            ProviderFactor(
                id=uuid.UUID(str(value["id"])),
                status=str(value.get("status") or ""),
                factor_type=str(value.get("factor_type") or value.get("type") or ""),
                friendly_name=str(value.get("friendly_name") or "Authenticator"),
                created_at=_date(value.get("created_at")),
                updated_at=_date(value.get("updated_at")),
                last_challenged_at=_date(value.get("last_challenged_at")),
            )
            for value in values
        ]

    async def admin_user(self, user_id: uuid.UUID) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"/admin/users/{user_id}",
            service_role=True,
        )

    async def enroll(
        self,
        access_token: str,
        friendly_name: str,
    ) -> EnrolledFactor:
        value = await self._request(
            "POST",
            "/factors",
            access_token=access_token,
            payload={
                "factor_type": "totp",
                "friendly_name": friendly_name,
                "issuer": "Jawnix",
            },
        )
        totp = value.get("totp") or {}
        return EnrolledFactor(
            id=uuid.UUID(str(value["id"])),
            friendly_name=str(value.get("friendly_name") or friendly_name),
            qr_code=str(totp.get("qr_code") or ""),
            secret=str(totp.get("secret") or ""),
            uri=str(totp.get("uri") or ""),
        )

    async def challenge(self, access_token: str, factor_id: uuid.UUID) -> uuid.UUID:
        value = await self._request(
            "POST",
            f"/factors/{factor_id}/challenge",
            access_token=access_token,
            payload={},
        )
        return uuid.UUID(str(value["id"]))

    async def verify(
        self,
        access_token: str,
        factor_id: uuid.UUID,
        challenge_id: uuid.UUID,
        code: str,
    ) -> ProviderSession:
        value = await self._request(
            "POST",
            f"/factors/{factor_id}/verify",
            access_token=access_token,
            payload={"challenge_id": str(challenge_id), "code": code},
        )
        return ProviderSession(
            access_token=str(value["access_token"]),
            refresh_token=str(value["refresh_token"]),
            expires_in=int(value.get("expires_in") or 0),
        )

    async def delete_factor(
        self,
        user_id: uuid.UUID,
        factor_id: uuid.UUID,
    ) -> None:
        await self._request(
            "DELETE",
            f"/admin/users/{user_id}/factors/{factor_id}",
            service_role=True,
        )

    async def logout(self, access_token: str) -> None:
        await self._request(
            "POST",
            "/logout?scope=global",
            access_token=access_token,
        )


def get_mfa_provider(settings: Settings) -> SupabaseMFAProvider:
    return SupabaseMFAProvider(settings)
