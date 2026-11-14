from __future__ import annotations

import hashlib
import re
import uuid
from fastapi import Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from ..activity import record_activity
from ..agency_management import (
    AgencyAssignmentConflict,
    agency_details,
    agency_directory,
    assign_customer,
    assignment_preview,
)
from ..auth import Principal, require_admin
from ..config import Settings, get_settings
from ..customer_accounts import (
    ProvisionResult,
    UserAccountConflict,
    cancel_user_account_invitation,
    invite_user_account,
    pending_invitation,
)
from ..customer_directory import (
    build_customer_details,
    build_customer_directory,
    customer_dependency_counts,
)
from ..customer_record import (
    LeadDatabaseChangeError,
    change_lead_database,
    customer_lead_reports,
    customer_summary,
)
from ..customer_history import remember_customer_name
from ..database import get_db
from ..niche_policy import (
    NichePolicyError,
    load_policy,
    normalize_policy_rows,
    replace_policy,
)
from ..models import (
    Agency,
    AgencyMembershipHistory,
    Customer,
    CustomerProfile,
    CustomerTombstone,
    DistributionEvent,
    LeadRequest,
    RequestStatus,
    UserAccount,
    CustomerRegistration,
    UserAccountInvitation,
    utcnow,
)
from ..schemas import (
    AgencyAssignment,
    AgencyCreate,
    AgencyUpdate,
    ActionReason,
    CustomerLeadDatabaseChange,
    CooldownWindowUpdate,
    CustomerUpdate,
    CustomerDelete,
    NichePolicyDraft,
    NichePolicyUpdate,
    CustomerDetailsOut,
    CustomerDirectoryOut,
    UserAccountInvite,
    UserAccountReplace,
)
from ..states import normalize_states
from . import _dispatch_invitation
from fastapi import APIRouter


router = APIRouter()


@router.get("/api/admin/customers/directory", response_model=CustomerDirectoryOut)
def admin_customer_directory(
    q: str = "",
    status: str = "active",
    agency_id: int | None = None,
    state: str = "",
    problems_only: bool = False,
    database: str = "",
    billing: str = "",
    needs_only: bool = False,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """The Customers table: every match, the ones that need something first."""
    return build_customer_directory(
        db,
        query=q,
        status=status,
        agency_id=agency_id,
        state=state,
        problems_only=problems_only,
        database=database,
        billing=billing,
        needs_only=needs_only,
    )


@router.get("/api/admin/agencies/directory")
def admin_agency_directory(
    q: str = "",
    status: str = "all",
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return agency_directory(db, query=q, status=status)


@router.post("/api/admin/agencies", status_code=201)
def create_agency(
    payload: AgencyCreate,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Agency name is required.")
    reason = payload.reason.strip()
    if not reason:
        raise HTTPException(status_code=422, detail="Reason is required.")
    base = re.sub(
        r"[^a-z0-9]+",
        "-",
        (payload.slug or name).lower(),
    ).strip("-")
    base = base[:70] or "agency"
    slug = base
    suffix = 2
    while db.scalar(select(Agency.id).where(Agency.slug == slug)):
        slug = f"{base}-{suffix}"
        suffix += 1
    agency = Agency(slug=slug, name=name, active=True)
    db.add(agency)
    db.flush()
    record_activity(
        db,
        action="agency_created",
        target_type="agency",
        target_id=agency.id,
        actor_id=principal.user_id,
        reason=reason,
        details={
            "before": None,
            "after": {
                "slug": agency.slug,
                "name": agency.name,
                "active": True,
            },
        },
    )
    db.commit()
    return {
        "ok": True,
        "agencyId": agency.id,
        "slug": agency.slug,
    }


@router.get("/api/admin/agencies/{agency_id}/details")
def admin_agency_details(
    agency_id: int,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    agency = db.get(Agency, agency_id)
    if agency is None or agency.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Agency was not found.")
    return agency_details(db, agency=agency)


@router.get(
    "/api/admin/customers/{customer_id}/agency-assignment-preview"
)
def admin_customer_agency_assignment_preview(
    customer_id: int,
    agency_id: int | None = None,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    customer = db.get(Customer, customer_id)
    if customer is None or customer.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Customer was not found.")
    destination = (
        db.get(Agency, agency_id) if agency_id is not None else None
    )
    if agency_id is not None and (
        destination is None or destination.deleted_at is not None
    ):
        raise HTTPException(status_code=404, detail="Agency was not found.")
    return assignment_preview(
        db,
        customer=customer,
        destination=destination,
        settings=settings,
    )


@router.post("/api/admin/customers/{customer_id}/agency-assignment")
def admin_customer_agency_assignment(
    customer_id: int,
    payload: AgencyAssignment,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    customer = db.scalar(
        select(Customer)
        .where(Customer.id == customer_id)
        .with_for_update()
    )
    if customer is None or customer.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Customer was not found.")
    destination = (
        db.scalar(
            select(Agency)
            .where(Agency.id == payload.agency_id)
            .with_for_update()
        )
        if payload.agency_id is not None
        else None
    )
    if payload.agency_id is not None and (
        destination is None or destination.deleted_at is not None
    ):
        raise HTTPException(status_code=404, detail="Agency was not found.")
    try:
        result = assign_customer(
            db,
            customer=customer,
            destination=destination,
            actor_id=principal.user_id,
            reason=payload.reason,
            confirmed=payload.confirmed,
            settings=settings,
        )
    except AgencyAssignmentConflict as conflict:
        raise HTTPException(status_code=409, detail=str(conflict)) from None
    db.commit()
    return result


@router.get(
    "/api/admin/customers/{customer_id}/details",
    response_model=CustomerDetailsOut,
)
def admin_customer_details(
    customer_id: int,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Read one Customer, with durable identity separated from its access."""
    customer = db.get(Customer, customer_id)
    if customer is None or customer.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Customer was not found.")
    return build_customer_details(db, customer=customer)


def _allocation_customer(db: Session, customer_id: int) -> Customer:
    customer = db.get(Customer, customer_id)
    if customer is None or customer.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Customer was not found.")
    return customer


@router.get("/api/admin/customers/{customer_id}/summary")
def admin_customer_summary(
    customer_id: int,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """The Customer page's numbers row and Needs list, in the table's terms."""
    return customer_summary(db, _allocation_customer(db, customer_id))


@router.get("/api/admin/customers/{customer_id}/lead-reports")
def admin_customer_lead_reports(
    customer_id: int,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Every Lead Report this Customer filed, newest first."""
    return customer_lead_reports(db, _allocation_customer(db, customer_id))


@router.put("/api/admin/customers/{customer_id}/lead-database")
def admin_customer_lead_database(
    customer_id: int,
    payload: CustomerLeadDatabaseChange,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Point a Customer's future allocations at DB1 or DB2, with a reason."""
    customer = db.scalar(
        select(Customer)
        .where(Customer.id == customer_id, Customer.deleted_at.is_(None))
        .with_for_update()
    )
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer was not found.")
    try:
        changed = change_lead_database(
            db,
            customer=customer,
            database=payload.lead_database,
            actor_id=principal.user_id,
            reason=payload.reason,
        )
    except LeadDatabaseChangeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    db.commit()
    return {"customerId": customer.id, "leadDatabase": customer.lead_database, "changed": changed}


@router.get("/api/admin/customers/{customer_id}/cooldown-window")
def get_cooldown_window(
    customer_id: int,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    customer = _allocation_customer(db, customer_id)
    return {"days": customer.cooldown_window_days}


@router.put("/api/admin/customers/{customer_id}/cooldown-window")
def update_cooldown_window(
    customer_id: int,
    payload: CooldownWindowUpdate,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    customer = _allocation_customer(db, customer_id)
    customer.cooldown_window_days = payload.days
    record_activity(
        db,
        action="cooldown_window_updated",
        target_type="customer",
        target_id=customer.id,
        actor_id=principal.user_id,
        reason=payload.reason,
        details={"days": payload.days},
    )
    db.commit()
    return {"days": customer.cooldown_window_days}


@router.get("/api/admin/customers/{customer_id}/niche-policy")
def get_niche_policy(
    customer_id: int,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _allocation_customer(db, customer_id)
    return {"rows": load_policy(db, customer_id)}


@router.put("/api/admin/customers/{customer_id}/niche-policy")
def update_niche_policy(
    customer_id: int,
    payload: NichePolicyUpdate,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    customer = _allocation_customer(db, customer_id)
    try:
        rows = replace_policy(
            db, customer_id, [row.model_dump() for row in payload.rows]
        )
    except NichePolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    record_activity(
        db,
        action="niche_policy_updated",
        target_type="customer",
        target_id=customer.id,
        actor_id=principal.user_id,
        reason=payload.reason,
        details={"rows": rows},
    )
    db.commit()
    return {"rows": rows}


@router.post("/api/admin/customers/{customer_id}/niche-policy/projected-availability")
def projected_niche_policy_availability(
    customer_id: int,
    payload: NichePolicyDraft,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    customer = _allocation_customer(db, customer_id)
    draft = [row.model_dump() for row in payload.rows]
    try:
        normalize_policy_rows(draft)
    except NichePolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    from ..pool_analytics import project_customer_availability

    return project_customer_availability(
        db,
        customer,
        settings,
        niche_policy_rows=draft,
    )


@router.get("/api/admin/customers/{customer_id}/availability")
def get_customer_availability(
    customer_id: int,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _allocation_customer(db, customer_id)
    from ..pool_analytics import read_customer_availability

    return read_customer_availability(db, customer_id)


@router.post("/api/admin/customers/{customer_id}/availability/refresh")
def refresh_customer_availability_endpoint(
    customer_id: int,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    customer = _allocation_customer(db, customer_id)
    from ..pool_analytics import refresh_customer_availability

    payload = refresh_customer_availability(db, customer, settings)
    db.commit()
    return payload


@router.put("/api/admin/customers/{customer_id}/user-account")
def replace_user_account(
    customer_id: int,
    payload: UserAccountReplace,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Provision a known authentication identity as the Customer's access.

    First provisioning takes effect immediately. Anything that would displace
    an existing active User Account is recorded as a pending invitation
    instead, so the Customer keeps working until the replacement is accepted.
    """
    customer = _locked_customer(db, customer_id)
    try:
        result = invite_user_account(
            db,
            customer=customer,
            auth_user_id=payload.auth_user_id,
            email=str(payload.email),
            actor_id=principal.user_id,
            reason=payload.reason,
        )
    except UserAccountConflict as conflict:
        raise HTTPException(
            status_code=409,
            detail=conflict.message,
        ) from None
    db.commit()
    return _provision_response(customer, result)


def _locked_customer(db: Session, customer_id: int) -> Customer:
    customer = db.scalar(
        select(Customer)
        .where(
            Customer.id == customer_id,
            Customer.deleted_at.is_(None),
        )
        .with_for_update()
    )
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer was not found.")
    return customer


def _provision_response(customer: Customer, result: ProvisionResult) -> dict:
    return {
        "customerId": customer.id,
        "authUserId": str(result.auth_user_id),
        "email": result.email,
        "licensedStates": normalize_states(customer.licensed_states),
        "activated": result.activated,
        "invitationId": (
            str(result.invitation_id) if result.invitation_id else None
        ),
        "replacesAuthUserId": (
            str(result.replaces_auth_user_id)
            if result.replaces_auth_user_id
            else None
        ),
    }


@router.post(
    "/api/admin/customers/{customer_id}/user-account-invitation",
    status_code=201,
)
async def invite_customer_user_account(
    customer_id: int,
    payload: UserAccountInvite,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Invite a replacement User Account without disturbing current access.

    The provider is asked first. If the invitation cannot be dispatched
    nothing is written, so a failed send leaves the Customer exactly as it
    was rather than stranding a half-made replacement.
    """
    customer = _locked_customer(db, customer_id)
    if not customer.active:
        raise HTTPException(
            status_code=409,
            detail=(
                "A User Account cannot be provisioned for a deactivated "
                "Customer."
            ),
        )
    if pending_invitation(db, customer.id) is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                "This Customer already has an outstanding User Account "
                "invitation. Cancel it before inviting a different account."
            ),
        )
    auth_user_id = await _dispatch_invitation(settings, payload)
    try:
        result = invite_user_account(
            db,
            customer=customer,
            auth_user_id=auth_user_id,
            email=str(payload.email),
            actor_id=principal.user_id,
            reason=payload.reason,
        )
    except UserAccountConflict as conflict:
        raise HTTPException(
            status_code=409,
            detail=conflict.message,
        ) from None
    db.commit()
    return _provision_response(customer, result)


@router.delete("/api/admin/customers/{customer_id}/user-account-invitation")
def cancel_customer_user_account_invitation(
    customer_id: int,
    payload: ActionReason,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Withdraw an outstanding invitation. Current access is untouched."""
    customer = _locked_customer(db, customer_id)
    invitation = cancel_user_account_invitation(
        db,
        customer=customer,
        actor_id=principal.user_id,
        reason=payload.reason,
    )
    if invitation is None:
        raise HTTPException(
            status_code=404,
            detail="This Customer has no outstanding invitation.",
        )
    db.commit()
    return {"ok": True, "invitationId": str(invitation.id)}


@router.patch("/api/admin/agencies/{agency_id}")
def update_agency(
    agency_id: int,
    payload: AgencyUpdate,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    agency = db.get(Agency, agency_id)
    if agency is None or agency.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Agency was not found.")
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Agency name is required.")
    reason = payload.reason.strip()
    if not reason:
        raise HTTPException(status_code=422, detail="Reason is required.")
    previous = {
        "name": agency.name,
        "active": agency.active,
    }
    agency.name = name
    agency.active = payload.active
    after = {
        "name": agency.name,
        "active": agency.active,
    }
    if previous != after:
        record_activity(
            db,
            action="agency_updated",
            target_type="agency",
            target_id=agency.id,
            actor_id=principal.user_id,
            reason=reason,
            details={
                "before": previous,
                "after": after,
            },
        )
    db.commit()
    return {"ok": True}


@router.patch("/api/admin/customers/{customer_id}")
def update_customer(
    customer_id: int,
    payload: CustomerUpdate,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    customer = db.get(Customer, customer_id)
    if customer is None or customer.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Customer was not found.")
    if payload.agency_id != customer.agency_id:
        raise HTTPException(
            status_code=409,
            detail=(
                "Agency membership changes require a consequence preview "
                "and explicit confirmation."
            ),
        )
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Customer name is required.")
    previous = {
        "name": customer.name,
        "active": customer.active,
        "agencyId": customer.agency_id,
    }
    customer.name = name
    customer.active = payload.active
    after = {
        "name": customer.name,
        "active": customer.active,
        "agencyId": customer.agency_id,
    }
    if previous != after:
        record_activity(
            db,
            action="customer_updated",
            target_type="customer",
            target_id=customer.id,
            actor_id=principal.user_id,
            reason=payload.reason,
            details={
                "before": previous,
                "after": after,
            },
        )
    db.commit()
    return {"ok": True}


@router.delete("/api/admin/customers/{customer_id}")
def delete_customer(
    customer_id: int,
    payload: CustomerDelete,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    customer = db.scalar(
        select(Customer)
        .where(Customer.id == customer_id)
        .with_for_update()
    )
    if customer is None or customer.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Customer was not found.")
    if payload.confirm_slug != customer.slug:
        raise HTTPException(
            status_code=409,
            detail="Customer slug confirmation did not match.",
        )
    if customer.active:
        raise HTTPException(
            status_code=409,
            detail="Deactivate the Customer before deletion.",
        )
    dependencies = customer_dependency_counts(db, customer.id)
    if payload.hard_delete:
        if any(dependencies.values()):
            refused_customer_id = customer.id
            db.rollback()
            record_activity(
                db,
                action="customer_hard_delete_refused",
                target_type="customer",
                target_id=refused_customer_id,
                actor_id=principal.user_id,
                reason=payload.reason,
                details={
                    "before": {"deleted": False},
                    "after": {"deleted": False},
                    "guard": "dependent_history",
                    "dependencies": dependencies,
                },
            )
            db.commit()
            raise HTTPException(
                status_code=409,
                detail={
                    "message": (
                        "Hard deletion is blocked while Customer "
                        "dependencies exist."
                    ),
                    "dependencies": dependencies,
                },
            )
        record_activity(
            db,
            action="customer_hard_deleted",
            target_type="customer",
            target_id=customer.id,
            actor_id=principal.user_id,
            reason=payload.reason,
            details={
                "before": {"deleted": False},
                "after": {"deleted": True},
            },
        )
        db.delete(customer)
        db.commit()
        return {"ok": True, "hardDeleted": True}
    remember_customer_name(db, customer)
    for profile in db.scalars(
        select(CustomerProfile).where(
            CustomerProfile.agent_id == customer.id
        )
    ):
        profile.email = f"archived-{profile.user_id}@invalid.local"
        profile.customer_id = None
        profile.mapping_confirmed_at = None
    for account in db.scalars(
        select(UserAccount).where(
            UserAccount.customer_id == customer.id
        )
    ):
        account.active = False
        account.replaced_at = utcnow()
    # An outstanding invitation would otherwise still be acceptable and would
    # hand a removed Customer working access.
    cancel_user_account_invitation(
        db,
        customer=customer,
        actor_id=principal.user_id,
        reason=payload.reason,
    )
    customer.deleted_at = utcnow()
    record_activity(
        db,
        action="customer_deleted",
        target_type="customer",
        target_id=customer.id,
        actor_id=principal.user_id,
        reason=payload.reason,
        details={
            "before": {"deleted": False},
            "after": {
                "deleted": True,
                "historyPreserved": True,
            },
            "dependencies": dependencies,
        },
    )
    db.commit()
    return {
        "ok": True,
        "hardDeleted": False,
        "historyPreserved": True,
    }


@router.post("/api/admin/customers/{customer_id}/erase")
def erase_customer_personal_data(
    customer_id: int,
    payload: ActionReason,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    customer = db.scalar(
        select(Customer)
        .where(Customer.id == customer_id)
        .with_for_update()
    )
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer was not found.")
    if customer.active:
        raise HTTPException(
            status_code=409,
            detail="Deactivate the Customer before personal-data erasure.",
        )
    tombstone = db.scalar(
        select(CustomerTombstone).where(
            CustomerTombstone.former_customer_id == customer.id
        )
    )
    if tombstone is not None:
        return {
            "customerId": customer.id,
            "tombstoneId": str(tombstone.id),
            "duplicate": True,
        }
    opaque_key = hashlib.sha256(
        f"{customer.id}:{uuid.uuid4()}".encode("utf-8")
    ).hexdigest()
    tombstone = CustomerTombstone(
        former_customer_id=customer.id,
        opaque_key=opaque_key,
        actor_id=str(principal.user_id),
        reason=payload.reason.strip(),
    )
    db.add(tombstone)
    anonymous_email = f"erased-{opaque_key[:20]}@invalid.local"
    for profile in db.scalars(
        select(CustomerProfile).where(
            CustomerProfile.agent_id == customer.id
        )
    ):
        profile.email = anonymous_email
        profile.first_name = ""
        profile.last_name = ""
        profile.phone = ""
        profile.licensed_states = []
        profile.mapping_confirmed_at = None
    for account in db.scalars(
        select(UserAccount).where(
            UserAccount.customer_id == customer.id
        )
    ):
        account.email = anonymous_email
        account.active = False
        account.replaced_at = utcnow()
    for invitation in db.scalars(
        select(UserAccountInvitation).where(
            UserAccountInvitation.customer_id == customer.id
        )
    ):
        invitation.email = anonymous_email
        if invitation.status == "pending":
            invitation.status = "canceled"
            invitation.canceled_at = utcnow()
    original_slug = customer.slug
    for registration in db.scalars(select(CustomerRegistration).where(CustomerRegistration.customer_id == customer.id)):
        registration.email = anonymous_email
        registration.first_name = ""
        registration.last_name = ""
        registration.phone = ""
        registration.licensed_states = []
        registration.status = "rejected"
    customer.name = "Deleted Customer"
    customer.slug = f"deleted-customer-{customer.id}"
    customer.licensed_states = []
    customer.deleted_at = customer.deleted_at or utcnow()
    from ..models import CustomerHistoryIdentity
    identity = db.get(CustomerHistoryIdentity, customer.id)
    if identity is not None:
        db.delete(identity)
    record_activity(
        db,
        action="customer_personal_data_erased",
        target_type="customer",
        target_id=customer.id,
        actor_id=principal.user_id,
        reason=payload.reason,
        details={
            "before": {"personalDataErased": False},
            "after": {
                "personalDataErased": True,
                "tombstoneId": str(tombstone.id),
            },
            "formerCustomerId": customer.id,
            "formerSlugHash": hashlib.sha256(
                original_slug.encode("utf-8")
            ).hexdigest(),
        },
    )
    db.commit()
    return {
        "customerId": customer.id,
        "tombstoneId": str(tombstone.id),
        "historyPreserved": True,
    }


@router.delete("/api/admin/agencies/{agency_id}")
def delete_agency(
    agency_id: int,
    payload: CustomerDelete,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    agency = db.get(Agency, agency_id)
    if agency is None or agency.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Agency was not found.")
    if payload.confirm_slug != agency.slug:
        raise HTTPException(status_code=409, detail="Agency slug confirmation did not match.")
    if agency.active:
        raise HTTPException(
            status_code=409,
            detail="Deactivate the Agency before deletion.",
        )
    customers = list(
        db.scalars(
            select(Customer).where(Customer.agency_id == agency.id)
        )
    )
    active_customers = [
        customer.slug
        for customer in customers
        if customer.active and customer.deleted_at is None
    ]
    if active_customers:
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    "Deactivate every Customer before deleting the Agency."
                ),
                "activeCustomers": active_customers,
            },
        )
    customer_ids = [customer.id for customer in customers]
    distribution_count = int(
        db.scalar(
            select(func.count(DistributionEvent.id)).where(
                DistributionEvent.agency_id == agency.id
            )
        )
        or 0
    )
    if payload.hard_delete:
        dependencies = {
            "customers": len(customers),
            "distributions": distribution_count,
            "membershipHistory": int(
                db.scalar(
                    select(func.count(AgencyMembershipHistory.id)).where(
                        AgencyMembershipHistory.agency_id == agency.id
                    )
                )
                or 0
            ),
        }
        if any(dependencies.values()):
            refused_agency_id = agency.id
            db.rollback()
            record_activity(
                db,
                action="agency_hard_delete_refused",
                target_type="agency",
                target_id=refused_agency_id,
                actor_id=principal.user_id,
                reason=payload.reason,
                details={
                    "before": {"deleted": False},
                    "after": {"deleted": False},
                    "guard": "dependent_history",
                    "dependencies": dependencies,
                },
            )
            db.commit()
            raise HTTPException(
                status_code=409,
                detail={
                    "message": (
                        "Hard deletion is blocked while Agency "
                        "dependencies exist."
                    ),
                    "dependencies": dependencies,
                },
            )
        record_activity(
            db,
            action="agency_hard_deleted",
            target_type="agency",
            target_id=agency.id,
            actor_id=principal.user_id,
            reason=payload.reason,
            details={
                "before": {"deleted": False},
                "after": {"deleted": True},
            },
        )
        db.delete(agency)
        db.commit()
        return {"ok": True, "hardDeleted": True}
    active_requests = (
        int(
            db.scalar(
                select(func.count(LeadRequest.id)).where(
                    LeadRequest.agent_id.in_(customer_ids),
                    LeadRequest.status.notin_(
                        [
                            RequestStatus.delivered.value,
                            RequestStatus.rejected.value,
                            RequestStatus.canceled.value,
                        ]
                    ),
                )
            )
            or 0
        )
        if customer_ids
        else 0
    )
    if active_requests:
        raise HTTPException(
            status_code=409,
            detail=f"Resolve {active_requests} active request(s) before deleting this agency.",
        )
    profiles = (
        list(
            db.scalars(
                select(CustomerProfile).where(
                    CustomerProfile.agent_id.in_(customer_ids)
                )
            )
        )
        if customer_ids
        else []
    )
    for profile in profiles:
        profile.customer_id = None
        profile.mapping_confirmed_at = None
    now = utcnow()
    deleted_customers = 0
    for customer in customers:
        if customer.deleted_at is None:
            deleted_customers += 1
        customer.active = False
        customer.deleted_at = customer.deleted_at or now
    agency.active = False
    agency.deleted_at = now
    record_activity(
        db,
        action="agency_deleted",
        target_type="agency",
        target_id=agency.id,
        actor_id=principal.user_id,
        reason=payload.reason,
        details={
            "before": {"deleted": False},
            "after": {
                "deleted": True,
                "historyPreserved": True,
            },
            "deletedCustomers": deleted_customers,
        },
    )
    db.commit()
    return {
        "ok": True,
        "deletedCustomers": deleted_customers,
        "unassignedRecipients": len(profiles),
        "historyPreserved": True,
    }
