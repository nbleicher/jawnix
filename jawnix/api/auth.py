from __future__ import annotations

import uuid
from fastapi import Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..auth import (
    Principal,
    administration_home,
    clear_session,
    require_admin,
    require_principal,
)
from ..config import Settings, get_settings
from ..customer_accounts import UserAccountConflict
from ..customer_history import archived_customer_matches
from ..customer_registration import (
    CUSTOMER_HOME_PATH,
    decide_registration,
    registration_next,
    registration_status,
    set_registration_states,
)
from ..database import get_db
from ..models import CustomerRegistration
from ..schemas import RegistrationDecision, RegistrationStates
from ..scraper_proxy import clear_scraper_grant
from fastapi import APIRouter


router = APIRouter()


@router.get("/api/auth/signup-options")
def signup_options():
    from ..states import US_STATE_OPTIONS
    return {"states": [{"code": code, "name": name} for code, name in US_STATE_OPTIONS]}


@router.get("/api/me/registration")
def my_registration(principal: Principal = Depends(require_principal), db: Session = Depends(get_db)):
    item = db.get(CustomerRegistration, principal.user_id)
    if item is None:
        return {"status": "active", "next": administration_home(principal) if principal.role == "admin" else CUSTOMER_HOME_PATH}
    return registration_status(item)


@router.post("/api/me/registration/states")
def complete_signup_states(payload: RegistrationStates, principal: Principal = Depends(require_principal), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    if principal.role != "customer":
        raise HTTPException(status_code=403, detail="Only customers can complete signup state setup.")
    try:
        result = set_registration_states(db, principal.user_id, payload.states, settings)
    except UserAccountConflict as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=error.message) from None
    db.commit()
    return registration_status(result)


@router.get("/api/admin/registrations")
def pending_registrations(_: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    return [{**registration_status(item), "id": str(item.auth_user_id),
             "matches": archived_customer_matches(db, item.first_name, item.last_name)}
            for item in db.scalars(select(CustomerRegistration).where(CustomerRegistration.status == "awaiting_review").order_by(CustomerRegistration.created_at)) if item.licensed_states]


@router.post("/api/admin/registrations/{user_id}/decision")
def decide_signup(user_id: uuid.UUID, payload: "RegistrationDecision", principal: Principal = Depends(require_admin), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    try:
        result = decide_registration(db, user_id, payload, str(principal.user_id), settings)
    except UserAccountConflict as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=error.message) from None
    db.commit()
    return registration_status(result)


@router.get("/api/auth/destination")
def authentication_destination(principal: Principal = Depends(require_principal), db: Session = Depends(get_db)):
    registration = db.get(CustomerRegistration, principal.user_id)
    if principal.role == "customer" and registration is not None:
        return {"next": registration_next(registration)}
    return {"next": administration_home(principal) if principal.role == "admin" else CUSTOMER_HOME_PATH}


@router.post("/api/auth/logout")
def logout(
    response: Response,
    _: Principal = Depends(require_principal),
    settings: Settings = Depends(get_settings),
):
    clear_session(response, settings)
    # The Scraper Privileged Session belongs to this sign-in and ends with it.
    clear_scraper_grant(response, settings)
    return {"ok": True}
