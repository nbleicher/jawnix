from __future__ import annotations

import hmac

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

security = HTTPBearer(auto_error=False)


def valid_control_token(request: Request, authorization: str | None) -> bool:
    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not supplied:
        return False
    expected = request.app.state.settings.scraper_control_token.get_secret_value()
    return hmac.compare_digest(supplied, expected)


def require_control_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> None:
    authorization = (
        f"{credentials.scheme} {credentials.credentials}"
        if credentials is not None
        else None
    )
    valid = valid_control_token(request, authorization)
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )


# The old dashboard routers remain in the imported snapshot as migration
# reference, but the running service no longer includes them.  Keeping this
# alias avoids making those dormant modules unimportable while Stage A lands.
require_user = require_control_token
