"""The administrator's read model for Customers and their access.

Administration used to start from the hierarchy and edit downward. This module
starts from the Customer instead: find one, then read its details with durable
identity and replaceable access presented as two separate things.

Everything returned here is presentation-ready. The screens render labels,
tones, and hrefs directly so no surface has to re-derive what a lifecycle
value means.
"""

from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .customer_accounts import active_user_account, pending_invitation
from .models import (
    Agency,
    AgencyMembershipHistory,
    AuditEntry,
    Customer,
    CustomerProfile,
    CustomerTombstone,
    DistributionEvent,
    LeadDispositionTransition,
    LeadOutcome,
    LeadReport,
    LeadRequest,
    UserAccount,
    UserAccountInvitation,
)
from .schemas import (
    CustomerActivityEntry,
    CustomerAdminStatus,
    CustomerDeletionGuard,
    CustomerDetailsOut,
    CustomerDirectoryAgency,
    CustomerDirectoryFilters,
    CustomerDirectoryOut,
    CustomerDirectoryRow,
    CustomerHistory,
    CustomerRecord,
    UserAccountInvitationRecord,
    UserAccountRecord,
)
from .states import US_STATES


_ACTIVE_CUSTOMER = CustomerAdminStatus(
    label="Active",
    description="This Customer can request and receive Batches.",
    tone="success",
)
_DEACTIVATED_CUSTOMER = CustomerAdminStatus(
    label="Deactivated",
    description=(
        "Blocked from sign-in and new Batch Requests. History is retained."
    ),
    tone="warning",
)
_ACTIVE_ACCOUNT = CustomerAdminStatus(
    label="Account active",
    description="One User Account can sign in for this Customer.",
    tone="success",
)
_INVITED_ACCOUNT = CustomerAdminStatus(
    label="Replacement invited",
    description=(
        "The current User Account stays active until the invitation is "
        "accepted."
    ),
    tone="info",
)
_FIRST_INVITE_PENDING = CustomerAdminStatus(
    label="Invitation sent",
    description="Nobody can sign in until the invitation is accepted.",
    tone="warning",
)
_NO_ACCOUNT = CustomerAdminStatus(
    label="No account",
    description="Nobody can sign in for this Customer yet.",
    tone="danger",
)
_PENDING_INVITATION = CustomerAdminStatus(
    label="Awaiting acceptance",
    description=(
        "Access changes hands only when the invited person accepts."
    ),
    tone="info",
)

#: Audit actions worth surfacing on Customer details, in administrator words.
_ACTIVITY_LABELS: dict[str, str] = {
    "customer_created": "Customer created",
    "customer_updated": "Customer updated",
    "customer_agency_assignment_changed": "Agency assignment changed",
    "customer_deleted": "Customer removed from active use",
    "customer_hard_deleted": "Customer permanently deleted",
    "customer_hard_delete_refused": "Permanent deletion refused",
    "customer_personal_data_erased": "Personal data erased",
    "customer_user_account_provisioned": "First User Account provisioned",
    "customer_user_account_replaced": "User Account replaced",
    "user_account_invitation_sent": "User Account invitation sent",
    "user_account_invitation_canceled": "User Account invitation canceled",
    "user_account_customer_mapped": "User Account mapped to Customer",
    "user_account_password_reset_sent": "Password reset email sent",
}


def customer_dependency_counts(db: Session, customer_id: int) -> dict[str, int]:
    """Count every record that keeps a Customer from being truly deleted.

    Deletion stays honest only if this covers every restricting reference. A
    row counted here becomes visible evidence in a refusal; a row missed here
    becomes an unexplained database error.
    """
    counts = {
        "requests": select(func.count(LeadRequest.id)).where(
            LeadRequest.agent_id == customer_id
        ),
        "distributions": select(func.count(DistributionEvent.id)).where(
            DistributionEvent.agent_id == customer_id
        ),
        "outcomes": select(func.count(LeadOutcome.id)).where(
            LeadOutcome.customer_id == customer_id
        ),
        "reports": select(func.count(LeadReport.id)).where(
            LeadReport.customer_id == customer_id
        ),
        "dispositions": select(
            func.count(LeadDispositionTransition.id)
        ).where(LeadDispositionTransition.customer_id == customer_id),
        "profiles": select(func.count(CustomerProfile.user_id)).where(
            CustomerProfile.agent_id == customer_id
        ),
        "userAccounts": select(func.count(UserAccount.auth_user_id)).where(
            UserAccount.customer_id == customer_id
        ),
        "invitations": select(func.count(UserAccountInvitation.id)).where(
            UserAccountInvitation.customer_id == customer_id
        ),
        "agencyMemberships": select(
            func.count(AgencyMembershipHistory.id)
        ).where(AgencyMembershipHistory.customer_id == customer_id),
    }
    return {key: int(db.scalar(query) or 0) for key, query in counts.items()}


def build_customer_directory(
    db: Session,
    *,
    query: str = "",
    status: str = "all",
    agency_id: int | None = None,
    state: str = "",
    problems_only: bool = False,
) -> CustomerDirectoryOut:
    """Build the searchable Customer directory."""
    term = query.strip()
    state = state.strip().upper()
    if state and state not in US_STATES:
        state = ""
    if status not in {"all", "active", "deactivated"}:
        status = "all"

    selection = select(Customer).where(Customer.deleted_at.is_(None))
    if term:
        pattern = f"%{term.lower()}%"
        selection = selection.where(
            or_(
                func.lower(Customer.name).like(pattern),
                func.lower(Customer.slug).like(pattern),
                Customer.id.in_(
                    select(UserAccount.customer_id).where(
                        func.lower(UserAccount.email).like(pattern)
                    )
                ),
            )
        )
    if status == "active":
        selection = selection.where(Customer.active.is_(True))
    elif status == "deactivated":
        selection = selection.where(Customer.active.is_(False))
    if agency_id is not None:
        selection = selection.where(Customer.agency_id == agency_id)

    customers = list(db.scalars(selection.order_by(Customer.name, Customer.id)))
    accounts = _accounts_by_customer(db)
    invitations = _pending_invitations_by_customer(db)

    rows: list[CustomerDirectoryRow] = []
    for customer in customers:
        states = list(customer.licensed_states or [])
        if state and state not in states:
            continue
        account = accounts.get(customer.id)
        invitation = invitations.get(customer.id)
        problems = _setup_problems(customer, account, invitation)
        if problems_only and not problems:
            continue
        rows.append(
            CustomerDirectoryRow(
                id=customer.id,
                slug=customer.slug,
                name=customer.name,
                agency_id=customer.agency_id,
                agency=customer.agency.name if customer.agency else "",
                licensed_states=states,
                customer_status=(
                    _ACTIVE_CUSTOMER if customer.active
                    else _DEACTIVATED_CUSTOMER
                ),
                account_status=_account_status(account, invitation),
                account_email=account.email if account else "",
                last_activity_at=customer.last_fulfilled_at,
                problems=problems,
                href=f"/app/admin/customers/{customer.id}",
            )
        )

    agencies = [
        CustomerDirectoryAgency(
            id=agency.id,
            name=agency.name,
            active=agency.active,
        )
        for agency in db.scalars(
            select(Agency)
            .where(Agency.deleted_at.is_(None))
            .order_by(Agency.name)
        )
    ]
    total = int(
        db.scalar(
            select(func.count(Customer.id)).where(
                Customer.deleted_at.is_(None)
            )
        )
        or 0
    )
    return CustomerDirectoryOut(
        filters=CustomerDirectoryFilters(
            query=term,
            status=status,  # type: ignore[arg-type]
            agency_id=agency_id,
            state=state,
            problems_only=problems_only,
        ),
        agencies=agencies,
        states=sorted(US_STATES),
        total=total,
        matched=len(rows),
        customers=rows,
    )


def build_customer_details(
    db: Session,
    *,
    customer: Customer,
    activity_limit: int = 20,
) -> CustomerDetailsOut:
    """Build one Customer's details, keeping identity and access apart."""
    account = active_user_account(db, customer.id)
    invitation = pending_invitation(db, customer.id)
    profiles = {
        profile.user_id: profile
        for profile in db.scalars(
            select(CustomerProfile).where(
                CustomerProfile.agent_id == customer.id
            )
        )
    }
    former = [
        _account_record(item, profiles.get(item.auth_user_id))
        for item in db.scalars(
            select(UserAccount)
            .where(
                UserAccount.customer_id == customer.id,
                UserAccount.active.is_(False),
            )
            .order_by(UserAccount.replaced_at.desc(), UserAccount.created_at)
        )
    ]
    dependencies = customer_dependency_counts(db, customer.id)
    tombstoned = (
        db.scalar(
            select(func.count(CustomerTombstone.id)).where(
                CustomerTombstone.former_customer_id == customer.id
            )
        )
        or 0
    ) > 0
    return CustomerDetailsOut(
        customer=CustomerRecord(
            id=customer.id,
            slug=customer.slug,
            name=customer.name,
            agency_id=customer.agency_id,
            agency=customer.agency.name if customer.agency else "",
            active=customer.active,
            licensed_states=list(customer.licensed_states or []),
            status=(
                _ACTIVE_CUSTOMER if customer.active else _DEACTIVATED_CUSTOMER
            ),
            last_activity_at=customer.last_fulfilled_at,
        ),
        history=_history(db, customer.id, dependencies),
        user_account=(
            _account_record(account, profiles.get(account.auth_user_id))
            if account
            else None
        ),
        invitation=(
            UserAccountInvitationRecord(
                id=str(invitation.id),
                email=invitation.email,
                invited_at=invitation.created_at,
                replaces_auth_user_id=(
                    str(invitation.replaces_auth_user_id)
                    if invitation.replaces_auth_user_id
                    else None
                ),
                status=_PENDING_INVITATION,
            )
            if invitation
            else None
        ),
        former_accounts=former,
        activity=_activity(db, customer.id, activity_limit),
        deletion=CustomerDeletionGuard(
            dependencies=dependencies,
            requires_deactivation=customer.active,
            can_hard_delete=(
                not customer.active and not any(dependencies.values())
            ),
            tombstoned=tombstoned,
        ),
    )


def _accounts_by_customer(db: Session) -> dict[int, UserAccount]:
    return {
        account.customer_id: account
        for account in db.scalars(
            select(UserAccount).where(
                UserAccount.active.is_(True),
                UserAccount.customer_id.is_not(None),
            )
        )
        if account.customer_id is not None
    }


def _pending_invitations_by_customer(
    db: Session,
) -> dict[int, UserAccountInvitation]:
    return {
        invitation.customer_id: invitation
        for invitation in db.scalars(
            select(UserAccountInvitation).where(
                UserAccountInvitation.status == "pending"
            )
        )
    }


def _account_status(
    account: UserAccount | None,
    invitation: UserAccountInvitation | None,
) -> CustomerAdminStatus:
    if account is not None:
        return _INVITED_ACCOUNT if invitation else _ACTIVE_ACCOUNT
    return _FIRST_INVITE_PENDING if invitation else _NO_ACCOUNT


def _setup_problems(
    customer: Customer,
    account: UserAccount | None,
    invitation: UserAccountInvitation | None,
) -> list[str]:
    """Name what stops this Customer from working, in plain language."""
    problems: list[str] = []
    if account is None and invitation is None:
        problems.append("No User Account has been invited")
    elif account is None:
        problems.append("Invitation has not been accepted yet")
    if not customer.licensed_states:
        problems.append("No Licensed States")
    if customer.agency is not None and not customer.agency.active:
        problems.append("Agency is deactivated")
    return problems


def _history(
    db: Session,
    customer_id: int,
    dependencies: dict[str, int],
) -> CustomerHistory:
    bounds = db.execute(
        select(
            func.min(DistributionEvent.delivered_at),
            func.max(DistributionEvent.delivered_at),
        ).where(DistributionEvent.agent_id == customer_id)
    ).one()
    return CustomerHistory(
        requests=dependencies["requests"],
        distributions=dependencies["distributions"],
        outcomes=dependencies["outcomes"],
        reports=dependencies["reports"],
        first_delivered_at=bounds[0],
        last_delivered_at=bounds[1],
    )


def _account_record(
    account: UserAccount,
    profile: CustomerProfile | None,
) -> UserAccountRecord:
    name = ""
    if profile is not None:
        name = " ".join(
            part
            for part in (profile.first_name, profile.last_name)
            if part
        ).strip()
    return UserAccountRecord(
        auth_user_id=str(account.auth_user_id),
        email=account.email,
        name=name,
        active=account.active,
        created_at=account.created_at,
        replaced_at=account.replaced_at,
        replaced_by_auth_user_id=(
            str(account.replaced_by_auth_user_id)
            if account.replaced_by_auth_user_id
            else None
        ),
    )


def _activity(
    db: Session,
    customer_id: int,
    limit: int,
) -> list[CustomerActivityEntry]:
    entries = db.scalars(
        select(AuditEntry)
        .where(
            AuditEntry.target_type == "customer",
            AuditEntry.target_id == str(customer_id),
        )
        .order_by(AuditEntry.created_at.desc(), AuditEntry.id)
        .limit(limit)
    )
    return [
        CustomerActivityEntry(
            id=str(entry.id),
            action=entry.action,
            label=_ACTIVITY_LABELS.get(
                entry.action,
                entry.action.replace("_", " ").capitalize(),
            ),
            actor=entry.actor_user_id,
            reason=entry.reason,
            created_at=entry.created_at,
        )
        for entry in entries
    ]

