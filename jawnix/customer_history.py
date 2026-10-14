"""Names propose a durable Customer restoration. Only the operator approves it."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from .models import (
    Customer,
    CustomerHistoryIdentity,
    CustomerProfile,
    CustomerTombstone,
    LeadRequest,
    UserAccount,
)


def remember_customer_name(db: Session, customer: Customer) -> None:
    profile = db.scalar(
        select(CustomerProfile)
        .where(
            CustomerProfile.agent_id == customer.id,
            CustomerProfile.first_name != "",
            CustomerProfile.last_name != "",
        )
        .order_by(CustomerProfile.updated_at.desc(), CustomerProfile.user_id)
        .limit(1)
    )
    if profile is not None:
        first, last = profile.first_name.strip(), profile.last_name.strip()
    else:
        first, _, last = customer.name.strip().partition(" ")
        last = last.strip()
    if not first or not last or len(first) > 120 or len(last) > 120:
        return
    identity = db.get(CustomerHistoryIdentity, customer.id)
    if identity is None:
        db.add(CustomerHistoryIdentity(
            customer_id=customer.id, first_name=first, last_name=last
        ))
    else:
        identity.first_name, identity.last_name = first, last


def archived_customer_matches(
    db: Session, first_name: str, last_name: str
) -> list[dict]:
    first_name, last_name = first_name.strip(), last_name.strip()
    if not first_name or not last_name:
        return []
    customers = db.scalars(
        select(Customer)
        .join(CustomerHistoryIdentity)
        .where(
            Customer.deleted_at.is_not(None),
            Customer.active.is_(False),
            CustomerHistoryIdentity.first_name == first_name,
            CustomerHistoryIdentity.last_name == last_name,
            ~select(CustomerTombstone.id)
            .where(CustomerTombstone.former_customer_id == Customer.id)
            .exists(),
        )
        .order_by(Customer.id)
    )
    result = []
    for customer in customers:
        email = db.scalar(
            select(UserAccount.email)
            .where(UserAccount.customer_id == customer.id)
            .order_by(UserAccount.created_at.desc(), UserAccount.auth_user_id)
            .limit(1)
        )
        result.append({
            "id": customer.id, "name": customer.name,
            "slug": customer.slug, "email": email or "",
        })
    return result


def request_history_scope(
    profile: CustomerProfile, user_id: UUID
) -> ColumnElement[bool]:
    # An unmapped login can see only its own submissions, never every NULL
    # customer. A mapped replacement inherits the durable party's requests.
    if profile.customer_id is not None:
        return LeadRequest.agent_id == profile.customer_id
    return LeadRequest.user_id == user_id
