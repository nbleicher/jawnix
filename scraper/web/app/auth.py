from __future__ import annotations

import hmac
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

security = HTTPBasic(auto_error=False)


def require_user(
    request: Request,
    credentials: Optional[HTTPBasicCredentials] = Depends(security),
) -> str:
    settings = request.app.state.settings
    valid = credentials is not None
    if credentials is not None:
        valid = hmac.compare_digest(credentials.username, settings.dash_username)
        valid = valid and hmac.compare_digest(credentials.password, settings.dash_password)
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username
