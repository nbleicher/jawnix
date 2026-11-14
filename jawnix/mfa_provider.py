"""The sole boundary between Jawnix and the identity provider's MFA bridge.

No caller outside this module knows provider endpoint paths or forwards a
provider error body.  In particular, TOTP secrets and submitted codes are never
included in an exception, log message, or audit entry.
"""

from __future__ import annotations

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
        expired_session: bool = False,
    ):
        super().__init__(message)
        self.invalid_code = invalid_code
        self.expired_session = expired_session


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
    backup_codes: tuple[str, ...] = ()
    session: ProviderSession | None = None


@dataclass(frozen=True)
class ProviderSession:
    access_token: str
    refresh_token: str
    expires_in: int


class BetterAuthMFAProvider:
    """Authenticated server bridge. Claims come from live provider sessions."""

    def __init__(self, settings: Settings):
        self.settings = settings

    async def request(self, action: str, **payload) -> Any:
        secret = self.settings.auth_bridge_secret.get_secret_value()
        if not self.settings.better_auth_url or not secret:
            raise MFAProviderError("The verification service is not configured.")
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    f"{self.settings.better_auth_url.rstrip('/')}/bridge",
                    headers={"Authorization": f"Bearer {secret}"},
                    json={"action": action, **payload},
                )
        except httpx.HTTPError:
            raise MFAProviderError() from None
        try:
            value = response.json()
        except ValueError:
            raise MFAProviderError() from None
        if response.status_code >= 400:
            raise MFAProviderError(invalid_code=bool(value.get("invalidCode")), expired_session=bool(value.get("expiredSession")))
        return value

    async def user_for_token(self, access_token: str) -> tuple[dict, dict]:
        value = await self.request("identity", accessToken=access_token)
        return value["user"], value["claims"]

    async def list_factors(self, user_id: uuid.UUID, *, session_id: str | None = None) -> list[ProviderFactor]:
        values = await self.request("factors", userId=str(user_id), sessionId=session_id)
        return [ProviderFactor(uuid.UUID(value["id"]), value["status"], "totp", value["friendly_name"], None, None, None) for value in values]

    async def admin_user(self, user_id: uuid.UUID) -> dict:
        return await self.request("admin-user", userId=str(user_id))

    async def enroll(self, access_token: str, friendly_name: str, *, password: str, replacement: bool = False) -> EnrolledFactor:
        value = await self.request("replace" if replacement else "enroll", accessToken=access_token, password=password)
        totp = value["totp"]
        token = value.get("session")
        return EnrolledFactor(uuid.UUID(value["id"]), friendly_name, totp["qr_code"], totp["secret"], totp["uri"], tuple(value["backup_codes"]), ProviderSession(**token) if token else None)

    async def challenge(self, access_token: str, factor_id: uuid.UUID) -> uuid.UUID:
        value = await self.request("challenge", accessToken=access_token, factorId=str(factor_id))
        return uuid.UUID(value["id"])

    async def verify(self, access_token: str, factor_id: uuid.UUID, challenge_id: uuid.UUID, code: str, *, method: str = "totp") -> ProviderSession:
        value = await self.request("verify", accessToken=access_token, factorId=str(factor_id), code=code, method=method)
        return ProviderSession(**value)

    async def delete_factor(self, user_id: uuid.UUID, factor_id: uuid.UUID) -> None:
        await self.request("delete-factor", userId=str(user_id), factorId=str(factor_id))

    async def logout(self, access_token: str) -> None:
        await self.request("logout", accessToken=access_token)


def get_mfa_provider(settings: Settings) -> BetterAuthMFAProvider:
    return BetterAuthMFAProvider(settings)
