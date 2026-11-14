from __future__ import annotations

import logging
import uuid
from pathlib import Path
from fastapi import HTTPException, Request, UploadFile
from sqlalchemy.orm import Session
from ..auth import Principal
from ..config import Settings
from ..models import CustomerProfile, UserAccount, CustomerRegistration


MAX_CSV_UPLOAD_BYTES = 10 * 1024 * 1024


_UPLOAD_CHUNK_BYTES = 1024 * 1024


class UploadTooLargeError(ValueError):
    pass


log = logging.getLogger("jawnix.api")


async def raw_body(request: Request) -> bytes:
    """Read the request body on the event loop so the route itself can stay a
    plain ``def`` and run its blocking work in the threadpool."""
    return await request.body()


def _current_customer_profile(
    db: Session,
    principal: Principal,
) -> CustomerProfile:
    """The signed-in Customer's profile, or the reason there is none.

    Every Customer screen crosses this: a replaced User Account is refused
    before it can read or write anything under its old identity.
    """

    # The administrator is never a Customer, even if a Customer profile was
    # left on that identity. The shell's 404 path sends them to administration.
    if principal.role == "admin":
        raise HTTPException(status_code=404, detail="Profile was not found.")
    registration = db.get(CustomerRegistration, principal.user_id)
    if registration is not None and registration.status != "active":
        raise HTTPException(status_code=409, detail="Your account setup or review is not complete.")
    account = db.get(UserAccount, principal.user_id)
    if account is not None and not account.active:
        raise HTTPException(
            status_code=403,
            detail="This User Account has been replaced.",
        )
    profile = db.get(CustomerProfile, principal.user_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile was not found.")
    return profile


def _mapped_customer_profile(
    db: Session,
    principal: Principal,
) -> CustomerProfile:
    profile = _current_customer_profile(db, principal)
    if profile.customer_id is None:
        raise HTTPException(status_code=404, detail="Customer was not found.")
    return profile


def _store_exclusion_upload(
    upload: UploadFile,
    *,
    exclusion_list_id: uuid.UUID,
    settings: Settings,
) -> Path:
    directory = Path(settings.batch_dir) / "exclusion-lists"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{exclusion_list_id}.csv"
    _store_bounded_upload(upload, path)
    return path


def _store_bounded_upload(upload: UploadFile, path: Path) -> None:
    written = 0
    try:
        with path.open("xb") as stream:
            while chunk := upload.file.read(_UPLOAD_CHUNK_BYTES):
                written += len(chunk)
                if written > MAX_CSV_UPLOAD_BYTES:
                    raise UploadTooLargeError
                stream.write(chunk)
    except Exception:
        path.unlink(missing_ok=True)
        raise
