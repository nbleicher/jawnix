"""User Account provisioning for durable Customers.

A Customer is durable; a User Account is replaceable authentication. This
module owns the seam between them, so no caller can accidentally treat
replacing access as replacing the party that owns the distribution history.

Two rules shape everything here:

* Exactly one active User Account per Customer. The partial unique index
  ``uq_active_user_account_per_customer`` is the real enforcement -- the guards
  below exist to turn a violation into a clear refusal instead of an
  IntegrityError, not to stand in for the constraint.
* A replacement never lands until it is accepted. Inviting prepares the swap;
  acceptance performs it. In between, the Customer keeps working through the
  account it already had.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .activity import record_activity
from .models import (
    Customer,
    CustomerProfile,
    UserAccount,
    UserAccountInvitation,
    utcnow,
)
from .states import normalize_states


class UserAccountConflict(Exception):
    """A User Account change was refused by a durable-identity rule."""

    def __init__(self, message: str, *, guard: str) -> None:
        super().__init__(message)
        self.message = message
        self.guard = guard


@dataclass(frozen=True)
class ProvisionResult:
    """What an invitation actually did to the Customer's access."""

    customer_id: int
    auth_user_id: uuid.UUID
    email: str
    # True only on first provisioning, where there was no account to displace
    # and therefore nothing to keep working in the meantime.
    activated: bool
    invitation_id: uuid.UUID | None
    replaces_auth_user_id: uuid.UUID | None


def active_user_account(db: Session, customer_id: int) -> UserAccount | None:
    """Return the Customer's one active User Account, if it has one."""
    return db.scalar(
        select(UserAccount).where(
            UserAccount.customer_id == customer_id,
            UserAccount.active.is_(True),
        )
    )


def pending_invitation(
    db: Session,
    customer_id: int,
) -> UserAccountInvitation | None:
    """Return the Customer's outstanding invitation, if it has one."""
    return db.scalar(
        select(UserAccountInvitation).where(
            UserAccountInvitation.customer_id == customer_id,
            UserAccountInvitation.status == "pending",
        )
    )


def invite_user_account(
    db: Session,
    *,
    customer: Customer,
    auth_user_id: uuid.UUID,
    email: str,
    actor_id: object,
    reason: str,
) -> ProvisionResult:
    """Provision access to ``customer`` for an invited authentication identity.

    First provisioning activates immediately because there is nothing to
    displace. Every later invitation is only a promise: it is recorded as
    pending and the Customer's existing User Account stays active until the
    invited person accepts.

    Does not commit -- the caller commits the change and its Activity evidence
    together.
    """
    email = email.strip().lower()
    if customer.deleted_at is not None or not customer.active:
        raise UserAccountConflict(
            "A User Account cannot be provisioned for a deactivated Customer.",
            guard="inactive_customer",
        )

    accounts = _locked_accounts(db, customer.id)
    current = next(
        (account for account in accounts if account.active),
        None,
    )
    _refuse_foreign_account(db, auth_user_id, customer.id)

    if current is not None and current.auth_user_id == auth_user_id:
        # Already the Customer's access. Re-confirm the mapping so a repeated
        # request is a no-op rather than a second invitation.
        _confirm_profile(db, customer, current.auth_user_id, email)
        current.email = email
        return ProvisionResult(
            customer_id=customer.id,
            auth_user_id=auth_user_id,
            email=email,
            activated=True,
            invitation_id=None,
            replaces_auth_user_id=None,
        )

    outstanding = pending_invitation(db, customer.id)
    if outstanding is not None:
        if outstanding.auth_user_id == auth_user_id:
            return ProvisionResult(
                customer_id=customer.id,
                auth_user_id=auth_user_id,
                email=outstanding.email,
                activated=False,
                invitation_id=outstanding.id,
                replaces_auth_user_id=outstanding.replaces_auth_user_id,
            )
        raise UserAccountConflict(
            "This Customer already has an outstanding User Account "
            "invitation. Cancel it before inviting a different account.",
            guard="pending_invitation",
        )

    if current is None:
        account = _activate(db, customer, auth_user_id, email, utcnow())
        record_activity(
            db,
            action="customer_user_account_provisioned",
            target_type="customer",
            target_id=customer.id,
            actor_id=actor_id,
            reason=reason,
            details={
                "before": {"activeAuthUserIds": []},
                "after": {"activeAuthUserIds": [str(account.auth_user_id)]},
                "invitationDispatched": True,
            },
        )
        return ProvisionResult(
            customer_id=customer.id,
            auth_user_id=auth_user_id,
            email=email,
            activated=True,
            invitation_id=None,
            replaces_auth_user_id=None,
        )

    invitation = UserAccountInvitation(
        customer_id=customer.id,
        auth_user_id=auth_user_id,
        email=email,
        status="pending",
        replaces_auth_user_id=current.auth_user_id,
        invited_by=str(actor_id),
        reason=reason,
    )
    db.add(invitation)
    db.flush()
    record_activity(
        db,
        action="user_account_invitation_sent",
        target_type="customer",
        target_id=customer.id,
        actor_id=actor_id,
        reason=reason,
        details={
            "before": {"activeAuthUserIds": [str(current.auth_user_id)]},
            # Nothing about access changed yet, and that is the point.
            "after": {"activeAuthUserIds": [str(current.auth_user_id)]},
            "invitationId": str(invitation.id),
            "invitedAuthUserId": str(auth_user_id),
            "replacesAuthUserId": str(current.auth_user_id),
        },
    )
    return ProvisionResult(
        customer_id=customer.id,
        auth_user_id=auth_user_id,
        email=email,
        activated=False,
        invitation_id=invitation.id,
        replaces_auth_user_id=current.auth_user_id,
    )


def accept_user_account_invitation(
    db: Session,
    *,
    auth_user_id: uuid.UUID,
    email: str,
) -> UserAccountInvitation | None:
    """Complete a pending invitation for the signing-in identity.

    This is the only moment access changes hands. Activation and deactivation
    happen in one transaction so the Customer is never without an account and
    never has two. Returns ``None`` when the identity has no invitation
    waiting, which is the ordinary case for every other sign-in.

    Does not commit.
    """
    invitation = db.scalar(
        select(UserAccountInvitation)
        .where(
            UserAccountInvitation.auth_user_id == auth_user_id,
            UserAccountInvitation.status == "pending",
        )
        .with_for_update()
    )
    if invitation is None:
        return None

    customer = db.scalar(
        select(Customer)
        .where(Customer.id == invitation.customer_id)
        .with_for_update()
    )
    if customer is None or customer.deleted_at is not None:
        raise UserAccountConflict(
            "The invited Customer is no longer available.",
            guard="missing_customer",
        )
    if not customer.active:
        raise UserAccountConflict(
            "This invitation belongs to a deactivated Customer.",
            guard="inactive_customer",
        )

    now = utcnow()
    superseded = [
        account
        for account in _locked_accounts(db, customer.id)
        if account.active and account.auth_user_id != auth_user_id
    ]
    # Retire the former account first. The one-active-account index is checked
    # per statement, so activating before deactivating would collide with the
    # very rule this swap exists to preserve.
    for account in superseded:
        account.active = False
        account.replaced_at = now
        account.replaced_by_auth_user_id = auth_user_id
    db.flush()

    _activate(db, customer, auth_user_id, email or invitation.email, now)
    invitation.status = "accepted"
    invitation.accepted_at = now
    record_activity(
        db,
        action="customer_user_account_replaced",
        target_type="customer",
        target_id=customer.id,
        actor_id=f"user_account:{auth_user_id}",
        reason="Invited User Account accepted its invitation.",
        details={
            "before": {
                "activeAuthUserIds": [
                    str(account.auth_user_id) for account in superseded
                ]
            },
            "after": {"activeAuthUserIds": [str(auth_user_id)]},
            "invitationId": str(invitation.id),
            # The durable party and its history are deliberately untouched.
            "customerId": customer.id,
            "historyPreserved": True,
        },
    )
    return invitation


def cancel_user_account_invitation(
    db: Session,
    *,
    customer: Customer,
    actor_id: object,
    reason: str,
) -> UserAccountInvitation | None:
    """Withdraw an outstanding invitation, leaving current access untouched.

    Does not commit. Returns ``None`` when nothing was outstanding.
    """
    invitation = db.scalar(
        select(UserAccountInvitation)
        .where(
            UserAccountInvitation.customer_id == customer.id,
            UserAccountInvitation.status == "pending",
        )
        .with_for_update()
    )
    if invitation is None:
        return None
    invitation.status = "canceled"
    invitation.canceled_at = utcnow()
    record_activity(
        db,
        action="user_account_invitation_canceled",
        target_type="customer",
        target_id=customer.id,
        actor_id=actor_id,
        reason=reason,
        details={
            "before": {"invitationStatus": "pending"},
            "after": {"invitationStatus": "canceled"},
            "invitationId": str(invitation.id),
            "invitedAuthUserId": str(invitation.auth_user_id),
        },
    )
    return invitation


def _locked_accounts(db: Session, customer_id: int) -> list[UserAccount]:
    return list(
        db.scalars(
            select(UserAccount)
            .where(UserAccount.customer_id == customer_id)
            .order_by(UserAccount.created_at, UserAccount.auth_user_id)
            .with_for_update()
        )
    )


def _refuse_foreign_account(
    db: Session,
    auth_user_id: uuid.UUID,
    customer_id: int,
) -> None:
    """Refuse an identity that is already spoken for by another Customer.

    Both checks mirror a partial unique index. Reaching them turns a
    constraint violation into an explanation the administrator can act on.
    """
    existing = db.get(UserAccount, auth_user_id)
    if (
        existing is not None
        and existing.active
        and existing.customer_id is not None
        and existing.customer_id != customer_id
    ):
        raise UserAccountConflict(
            "That User Account is already the active account for another "
            "Customer.",
            guard="account_in_use",
        )
    outstanding = db.scalar(
        select(UserAccountInvitation).where(
            UserAccountInvitation.auth_user_id == auth_user_id,
            UserAccountInvitation.status == "pending",
            UserAccountInvitation.customer_id != customer_id,
        )
    )
    if outstanding is not None:
        raise UserAccountConflict(
            "That email already has an outstanding invitation for another "
            "Customer.",
            guard="identity_invited_elsewhere",
        )


def _activate(
    db: Session,
    customer: Customer,
    auth_user_id: uuid.UUID,
    email: str,
    now: datetime,
) -> UserAccount:
    account = db.get(UserAccount, auth_user_id)
    if account is None:
        account = UserAccount(
            auth_user_id=auth_user_id,
            customer_id=customer.id,
            email=email,
            active=True,
        )
        db.add(account)
    else:
        account.customer_id = customer.id
        account.email = email
        account.active = True
        account.replaced_at = None
        account.replaced_by_auth_user_id = None
    _confirm_profile(db, customer, auth_user_id, email)
    db.flush()
    return account


def _confirm_profile(
    db: Session,
    customer: Customer,
    auth_user_id: uuid.UUID,
    email: str,
) -> CustomerProfile:
    """Point the authentication identity's profile at the durable Customer.

    Licensed States belong to the Customer, so the profile mirrors them rather
    than carrying its own answer into a replacement.
    """
    licensed_states = normalize_states(customer.licensed_states)
    profile = db.get(CustomerProfile, auth_user_id)
    if profile is None:
        profile = CustomerProfile(
            user_id=auth_user_id,
            email=email,
            licensed_states=licensed_states,
            agent_id=customer.id,
            mapping_confirmed_at=utcnow(),
        )
        db.add(profile)
    else:
        profile.email = email
        profile.licensed_states = licensed_states
        profile.customer_id = customer.id
        profile.mapping_confirmed_at = utcnow()
    return profile
