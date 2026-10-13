"""Read-only endpoint membership for acquisition yield measurement."""
from __future__ import annotations

import secrets
import hmac
import hashlib

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .database import get_db
from .models import Lead
from .states import US_STATES, normalize_phone

router = APIRouter(prefix="/api/internal/acquisition", tags=["internal"])


def membership_service_token(control_token: str) -> str:
    return hmac.new(control_token.encode(),b"acquisition-phone-membership-v1",hashlib.sha256).hexdigest()


def require_acquisition_service(request: Request, settings: Settings = Depends(get_settings)) -> None:
    expected = settings.scraper_control_token
    supplied = request.headers.get("authorization", "")
    if not expected or not secrets.compare_digest(supplied.encode(), ("Bearer " + membership_service_token(expected)).encode()):
        raise HTTPException(status_code=401, detail="Acquisition service authentication required")


class PhoneCandidate(BaseModel):
    phone: str = Field(max_length=80)
    title: str = Field(max_length=1000)
    state: str = Field(max_length=2)


class PhoneMembershipRequest(BaseModel):
    candidates: list[PhoneCandidate] = Field(max_length=2000)


class PhoneMembershipResponse(BaseModel):
    valid_phones: list[str]
    existing_phones: list[str]


@router.post("/phone-membership", response_model=PhoneMembershipResponse,
             dependencies=[Depends(require_acquisition_service)])
def phone_membership(payload: PhoneMembershipRequest, db: Session = Depends(get_db)):
    # Use the same endpoint identity and minimum listing criteria as Inventory Sync.
    valid = sorted({phone for row in payload.candidates
                    if (phone := normalize_phone(row.phone)) and row.title.strip()
                    and phone[0] in "23456789" and phone[3] in "23456789"
                    and row.state.upper() in US_STATES})
    existing = sorted(db.scalars(select(Lead.phone).where(Lead.phone.in_(valid)))) if valid else []
    # DB2-only endpoints remain in the shared canonical ledger and are included.
    return PhoneMembershipResponse(valid_phones=valid, existing_phones=existing)
