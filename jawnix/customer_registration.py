"""Public signup creates a party only after email verification and history review."""

import re
import uuid

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .activity import record_activity
from .customer_accounts import activate_registered_user_account, UserAccountConflict
from .customer_history import archived_customer_matches
from .models import Customer, CustomerProfile, CustomerRegistration, CustomerTombstone, LeadRequest, utcnow
from .schemas import RegistrationDecision, SignupProfile


def lock_registration_identity(db: Session, user_id: uuid.UUID) -> None:
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": user_id.int >> 65})


def _activate(db: Session, registration: CustomerRegistration, customer: Customer | None, actor_id: str) -> None:
    restored = customer is not None
    if customer is None:
        full_name = f"{registration.first_name} {registration.last_name}"
        slug = re.sub(r"[^a-z0-9]+", "-", full_name.lower()).strip("-")[:47] or "customer"
        customer = Customer(slug=f"{slug}-{registration.auth_user_id.hex}", name=full_name,
                            licensed_states=registration.licensed_states, active=True)
        db.add(customer)
        db.flush()
    else:
        # Archived requests must be resolved before restoring access with a
        # new state scope. Historical snapshots are never rewritten here.
        open_request = db.scalar(select(LeadRequest.id).where(
            LeadRequest.agent_id == customer.id,
            LeadRequest.status.notin_(["delivered", "rejected", "canceled"]),
        ).limit(1))
        if open_request is not None:
            raise UserAccountConflict("Resolve the archived Customer's outstanding requests before restoration.", guard="open_requests")
        customer.active = True
        customer.deleted_at = None
        customer.licensed_states = list(registration.licensed_states)
        if customer.agency is not None and (not customer.agency.active or customer.agency.deleted_at is not None):
            customer.agency_id = None
    activate_registered_user_account(db, customer=customer, auth_user_id=registration.auth_user_id, email=registration.email)
    registration.status = "active"
    registration.customer_id = customer.id
    registration.decided_at = utcnow()
    registration.decided_by = actor_id
    record_activity(db, action="customer_history_restored" if restored else "customer_registered",
                    target_type="customer", target_id=customer.id, actor_id=actor_id,
                    reason="Operator approved restoration for a verified signup." if restored else "Verified public signup activated a new Customer.",
                    details={"authUserId":str(registration.auth_user_id),"licensedStates":registration.licensed_states,
                             "historyPreserved":True,"operatorApproved":not actor_id.startswith("user_account:")})


def complete_registration(db: Session, *, user_id: uuid.UUID, email: str, details: SignupProfile) -> CustomerRegistration:
    """Caller has verified the provider token, customer role, and email ownership."""
    registration = db.get(CustomerRegistration, user_id)
    if registration is not None:
        return registration
    registration = CustomerRegistration(auth_user_id=user_id, email=email, **details.model_dump(), status="awaiting_review")
    db.add(registration)
    profile = db.get(CustomerProfile, user_id)
    if profile is None:
        profile = CustomerProfile(user_id=user_id, email=email, **details.model_dump())
        db.add(profile)
    else:
        for key, value in details.model_dump().items():
            setattr(profile, key, value)
    db.flush()
    matches = archived_customer_matches(db, details.first_name, details.last_name)
    if not matches:
        _activate(db, registration, None, f"user_account:{user_id}")
    else:
        record_activity(db, action="customer_registration_review_requested", target_type="user_account", target_id=user_id,
                        actor_id=f"user_account:{user_id}", reason="Verified signup needs an operator history decision.",
                        details={"matchingCustomerIds":[row["id"] for row in matches]})
    return registration


def decide_registration(db: Session, user_id: uuid.UUID, decision: RegistrationDecision, actor_id: str) -> CustomerRegistration:
    lock_registration_identity(db, user_id)
    registration = db.scalar(select(CustomerRegistration).where(CustomerRegistration.auth_user_id == user_id).with_for_update().execution_options(populate_existing=True))
    if registration is None or registration.status != "awaiting_review":
        raise UserAccountConflict("This registration is no longer awaiting review.", guard="registration_closed")
    customer = None
    if decision.decision == "restore":
        matches = archived_customer_matches(db, registration.first_name, registration.last_name)
        if decision.restore_customer_id not in {row["id"] for row in matches}:
            raise UserAccountConflict("Select a matching archived Customer.", guard="name_mismatch")
        customer = db.scalar(select(Customer).where(Customer.id == decision.restore_customer_id).with_for_update().execution_options(populate_existing=True))
        erased = db.scalar(select(CustomerTombstone.id).where(CustomerTombstone.former_customer_id == decision.restore_customer_id))
        if customer is None or customer.active or customer.deleted_at is None or erased is not None:
            raise UserAccountConflict("This archived Customer is no longer available.", guard="customer_unavailable")
    if decision.decision == "reject":
        registration.status = "rejected"
        registration.decided_at = utcnow()
        registration.decided_by = actor_id
    else:
        _activate(db, registration, customer, actor_id)
    record_activity(db, action="customer_registration_decided", target_type="user_account", target_id=user_id,
                    actor_id=actor_id, reason="Operator reviewed a matching-name registration.",
                    details={"decision":decision.decision,"customerId":registration.customer_id,"operatorApproved":True})
    return registration


def registration_status(registration: CustomerRegistration) -> dict:
    return {"status":registration.status,"firstName":registration.first_name,"lastName":registration.last_name,
            "email":registration.email,"phone":registration.phone,"states":registration.licensed_states,
            "next":"/app/overview" if registration.status == "active" else "/app/registration-status"}
