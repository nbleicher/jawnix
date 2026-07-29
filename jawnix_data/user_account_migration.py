"""Controlled one-time migration from legacy access to fresh User Accounts.

Dry-run is deliberately read-only. Apply is separately guarded, journals each
external invitation before dispatch, and finishes with content-addressed
reconciliation evidence.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import uuid
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol
from urllib.parse import urlencode

import httpx
from email_validator import EmailNotValidError, validate_email
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from jawnix.activity import record_activity
from jawnix.agency_management import (
    assign_customer,
    history_for_agency,
    history_for_customer,
)
from jawnix.config import Settings
from jawnix.customer_accounts import active_user_account, invite_user_account
from jawnix.models import (
    Agency,
    AgencyMembershipHistory,
    Customer,
    DistributionEvent,
    LeadRequest,
    UserAccount,
    UserAccountInvitation,
    UserAccountMigrationArtifact,
    UserAccountMigrationMapping,
    UserAccountMigrationRun,
    utcnow,
)


APPLY_CONFIRMATION = "APPLY-USER-ACCOUNT-MIGRATION"
REQUIRED_COLUMNS = {"customer", "email", "agency"}
REQUIRED_BACKUP_FIELDS = {
    "databaseSnapshot",
    "databaseDumpSha256",
    "resticCheckCompletedAt",
    "restoreRehearsalReference",
    "verifiedAt",
    "verifiedBy",
}
BACKUP_MAX_AGE = timedelta(hours=24)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class MigrationRefused(ValueError):
    """A safety gate refused dry-run approval or apply."""


@dataclass(frozen=True)
class ProviderIdentity:
    id: uuid.UUID
    email: str
    metadata: Mapping[str, object]


class MigrationIdentityProvider(Protocol):
    def list_users(self) -> list[ProviderIdentity]: ...

    def invite(
        self,
        email: str,
        *,
        run_id: uuid.UUID,
        mapping_id: uuid.UUID,
    ) -> ProviderIdentity: ...


class SupabaseMigrationIdentityProvider:
    """The narrow provider surface needed by the offline migration."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
    ) -> dict:
        if (
            not self.settings.supabase_url
            or not self.settings.supabase_service_role_key
        ):
            raise MigrationRefused(
                "Supabase URL and service-role key are required."
            )
        response = httpx.request(
            method,
            f"{self.settings.supabase_url.rstrip('/')}{path}",
            headers={
                "apikey": self.settings.supabase_service_role_key,
                "Authorization": (
                    f"Bearer {self.settings.supabase_service_role_key}"
                ),
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
        if response.status_code >= 300:
            raise RuntimeError(
                f"Supabase identity operation failed with HTTP "
                f"{response.status_code}."
            )
        return response.json()

    @staticmethod
    def _identity(value: Mapping[str, object]) -> ProviderIdentity:
        metadata = value.get("user_metadata") or value.get("raw_user_meta_data")
        return ProviderIdentity(
            id=uuid.UUID(str(value["id"])),
            email=str(value.get("email") or "").strip().lower(),
            metadata=metadata if isinstance(metadata, Mapping) else {},
        )

    def list_users(self) -> list[ProviderIdentity]:
        users: list[ProviderIdentity] = []
        page = 1
        while True:
            value = self._request(
                "GET",
                f"/auth/v1/admin/users?page={page}&per_page=1000",
            )
            batch = value.get("users") or []
            users.extend(self._identity(item) for item in batch)
            if len(batch) < 1000:
                return users
            page += 1

    def invite(
        self,
        email: str,
        *,
        run_id: uuid.UUID,
        mapping_id: uuid.UUID,
    ) -> ProviderIdentity:
        redirect_to = (
            f"{self.settings.public_base_url.rstrip('/')}"
            f"{'/app/accept-invitation' if self.settings.new_ui_enabled else '/portal-accept.html'}"
        )
        value = self._request(
            "POST",
            f"/auth/v1/invite?{urlencode({'redirect_to': redirect_to})}",
            {
                "email": email,
                "data": {
                    "jawnix_role": "customer",
                    "jawnix_migration_run_id": str(run_id),
                    "jawnix_migration_mapping_id": str(mapping_id),
                },
            },
        )
        return self._identity(value)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _checksum(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _file_checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalized_email(value: str) -> str:
    try:
        return validate_email(
            value.strip(),
            check_deliverability=False,
        ).normalized.lower()
    except EmailNotValidError as exc:
        raise ValueError(str(exc)) from None


#: Values in an optional ``migrate`` column that mean "include this row".
#: Anything else — "no", blank, a typo — excludes it. Opt-in rather than
#: opt-out: a row is only migrated when someone positively said so, so a
#: mistyped value skips a Customer rather than silently migrating one.
MIGRATE_YES = {"yes", "y", "true", "1"}


def _read_rows(path: Path) -> list[dict[str, object]]:
    with path.open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames is None or not REQUIRED_COLUMNS.issubset(
            set(reader.fieldnames)
        ):
            raise MigrationRefused(
                "Mapping CSV must contain customer, email, and agency columns."
            )
        # Present only if the draft generator wrote it, or someone added it.
        # Absent means every row migrates, preserving the original contract.
        gated = "migrate" in set(reader.fieldnames)
        rows: list[dict[str, object]] = []
        skipped = 0
        for row_number, raw in enumerate(reader, start=2):
            if gated and str(raw.get("migrate") or "").strip().casefold() not in MIGRATE_YES:
                skipped += 1
                continue
            rows.append(
                {
                    "rowNumber": row_number,
                    "customerSelector": str(raw.get("customer") or "").strip(),
                    "rawEmail": str(raw.get("email") or "").strip(),
                    "agencySelector": str(raw.get("agency") or "").strip(),
                }
            )
    if not rows:
        raise MigrationRefused(
            "Mapping CSV must contain at least one row marked migrate=yes."
            if skipped
            else "Mapping CSV must contain at least one row."
        )
    return rows


def _matches_customer(
    customers: list[Customer],
    selector: str,
) -> list[Customer]:
    folded = selector.casefold()
    numeric_id = int(selector) if selector.isdigit() else None
    return [
        customer
        for customer in customers
        if (
            (numeric_id is not None and customer.id == numeric_id)
            or customer.slug.casefold() == folded
            or customer.name.strip().casefold() == folded
        )
    ]


def _matches_agency(
    agencies: list[Agency],
    selector: str,
) -> list[Agency]:
    if selector.casefold() == "independent":
        return []
    folded = selector.casefold()
    numeric_id = int(selector) if selector.isdigit() else None
    return [
        agency
        for agency in agencies
        if (
            (numeric_id is not None and agency.id == numeric_id)
            or agency.slug.casefold() == folded
            or agency.name.strip().casefold() == folded
        )
    ]


def _history_lead_count(
    session: Session,
    *,
    customer_ids: set[int],
    agency_ids: set[int],
    cache: dict[tuple[frozenset[int], frozenset[int]], int],
) -> int:
    key = (frozenset(customer_ids), frozenset(agency_ids))
    if key in cache:
        return cache[key]
    clauses = []
    if customer_ids:
        clauses.append(DistributionEvent.agent_id.in_(customer_ids))
    if agency_ids:
        clauses.append(DistributionEvent.agency_id.in_(agency_ids))
    if not clauses:
        return 0
    count = int(
        session.scalar(
            select(func.count(func.distinct(DistributionEvent.lead_id))).where(
                or_(*clauses)
            )
        )
        or 0
    )
    cache[key] = count
    return count


def _history_counts(
    session: Session,
    customer: Customer,
    agency: Agency | None,
    *,
    lead_count_cache: dict[
        tuple[frozenset[int], frozenset[int]],
        int,
    ],
) -> dict[str, object]:
    customer_subjects = history_for_customer(session, customer)
    agency_subjects = (
        history_for_agency(session, agency) if agency is not None else None
    )
    combined_customers = set(customer_subjects.customer_ids)
    combined_agencies = set(customer_subjects.agency_ids)
    if agency_subjects is not None:
        combined_customers.update(agency_subjects.customer_ids)
        combined_agencies.update(agency_subjects.agency_ids)
    return {
        "customerDistributionEvents": int(
            session.scalar(
                select(func.count(DistributionEvent.id)).where(
                    DistributionEvent.agent_id == customer.id
                )
            )
            or 0
        ),
        "customerBatchRequests": int(
            session.scalar(
                select(func.count(LeadRequest.id)).where(
                    LeadRequest.agent_id == customer.id
                )
            )
            or 0
        ),
        "customerMembershipHistory": int(
            session.scalar(
                select(func.count(AgencyMembershipHistory.id)).where(
                    AgencyMembershipHistory.customer_id == customer.id
                )
            )
            or 0
        ),
        "permanentHistoryBefore": {
            "customers": len(customer_subjects.customer_ids),
            "agencies": len(customer_subjects.agency_ids),
            "distributedLeads": _history_lead_count(
                session,
                customer_ids=set(customer_subjects.customer_ids),
                agency_ids=set(customer_subjects.agency_ids),
                cache=lead_count_cache,
            ),
        },
        "destinationHistoryBefore": (
            {
                "customers": len(agency_subjects.customer_ids),
                "agencies": len(agency_subjects.agency_ids),
                "distributedLeads": _history_lead_count(
                    session,
                    customer_ids=set(agency_subjects.customer_ids),
                    agency_ids=set(agency_subjects.agency_ids),
                    cache=lead_count_cache,
                ),
            }
            if agency_subjects is not None
            else {"customers": 0, "agencies": 0, "distributedLeads": 0}
        ),
        "permanentHistoryAfter": {
            "customers": len(combined_customers),
            "agencies": len(combined_agencies),
            "distributedLeads": _history_lead_count(
                session,
                customer_ids=combined_customers,
                agency_ids=combined_agencies,
                cache=lead_count_cache,
            ),
        },
    }


def dry_run_user_account_migration(
    session: Session,
    provider: MigrationIdentityProvider,
    path: Path,
) -> dict[str, object]:
    """Return a deterministic preflight without flushing or committing."""

    source_rows = _read_rows(path)
    customers = list(
        session.scalars(
            select(Customer)
            .where(
                Customer.deleted_at.is_(None),
                Customer.active.is_(True),
            )
            .order_by(Customer.id)
        )
    )
    agencies = list(
        session.scalars(
            select(Agency)
            .where(Agency.deleted_at.is_(None))
            .order_by(Agency.id)
        )
    )
    accounts = list(session.scalars(select(UserAccount)))
    invitations = list(
        session.scalars(
            select(UserAccountInvitation).where(
                UserAccountInvitation.status == "pending"
            )
        )
    )
    provider_users = provider.list_users()

    local_by_email: dict[str, list[UserAccount]] = defaultdict(list)
    for account in accounts:
        local_by_email[account.email.strip().lower()].append(account)
    invite_by_email: dict[str, list[UserAccountInvitation]] = defaultdict(list)
    for invitation in invitations:
        invite_by_email[invitation.email.strip().lower()].append(invitation)
    provider_by_email: dict[str, list[ProviderIdentity]] = defaultdict(list)
    for identity in provider_users:
        if identity.email:
            provider_by_email[identity.email].append(identity)

    normalized_emails: list[str] = []
    for row in source_rows:
        try:
            normalized_emails.append(_normalized_email(str(row["rawEmail"])))
        except ValueError:
            normalized_emails.append(str(row["rawEmail"]).strip().lower())
    email_counts = Counter(normalized_emails)

    resolved_customers: list[int] = []
    report_rows: list[dict[str, object]] = []
    lead_count_cache: dict[
        tuple[frozenset[int], frozenset[int]],
        int,
    ] = {}
    for source, email in zip(source_rows, normalized_emails, strict=True):
        issues: list[dict[str, str]] = []
        selector = str(source["customerSelector"])
        matches = _matches_customer(customers, selector)
        customer = matches[0] if len(matches) == 1 else None
        if not selector or not matches:
            issues.append(
                {
                    "code": "missing_customer",
                    "detail": f"No active Customer matches {selector!r}.",
                }
            )
        elif len(matches) > 1:
            issues.append(
                {
                    "code": "ambiguous_customer",
                    "detail": f"More than one active Customer matches {selector!r}.",
                }
            )
        else:
            resolved_customers.append(customer.id)

        try:
            email = _normalized_email(str(source["rawEmail"]))
        except ValueError as exc:
            issues.append({"code": "invalid_email", "detail": str(exc)})
        if email_counts[email] > 1:
            issues.append(
                {
                    "code": "duplicate_email",
                    "detail": "The email appears more than once in the input.",
                }
            )

        agency_selector = str(source["agencySelector"])
        independent = agency_selector.casefold() == "independent"
        agency_matches = _matches_agency(agencies, agency_selector)
        agency = agency_matches[0] if len(agency_matches) == 1 else None
        if not independent and not agency_matches:
            issues.append(
                {
                    "code": "missing_agency",
                    "detail": f"No Agency matches {agency_selector!r}.",
                }
            )
        elif len(agency_matches) > 1:
            issues.append(
                {
                    "code": "ambiguous_agency",
                    "detail": f"More than one Agency matches {agency_selector!r}.",
                }
            )
        elif agency is not None and not agency.active:
            issues.append(
                {
                    "code": "inactive_agency",
                    "detail": "The destination Agency is deactivated.",
                }
            )

        email_conflicts: list[dict[str, object]] = []
        for account in local_by_email[email]:
            email_conflicts.append(
                {
                    "kind": "local_user_account",
                    "authUserId": str(account.auth_user_id),
                    "customerId": account.customer_id,
                    "active": account.active,
                }
            )
        for invitation in invite_by_email[email]:
            email_conflicts.append(
                {
                    "kind": "pending_invitation",
                    "authUserId": str(invitation.auth_user_id),
                    "customerId": invitation.customer_id,
                }
            )
        for identity in provider_by_email[email]:
            email_conflicts.append(
                {
                    "kind": "provider_identity",
                    "authUserId": str(identity.id),
                }
            )
        if email_conflicts:
            issues.append(
                {
                    "code": "account_conflict",
                    "detail": (
                        "The destination email already belongs to an account "
                        "or pending invitation."
                    ),
                }
            )

        current = (
            active_user_account(session, customer.id)
            if customer is not None
            else None
        )
        pending = (
            session.scalar(
                select(UserAccountInvitation).where(
                    UserAccountInvitation.customer_id == customer.id,
                    UserAccountInvitation.status == "pending",
                )
            )
            if customer is not None
            else None
        )
        if pending is not None:
            issues.append(
                {
                    "code": "customer_has_pending_invitation",
                    "detail": (
                        "The Customer already has an outstanding invitation."
                    ),
                }
            )

        counts = (
            _history_counts(
                session,
                customer,
                agency,
                lead_count_cache=lead_count_cache,
            )
            if customer is not None
            and (independent or len(agency_matches) == 1)
            else {}
        )
        report_rows.append(
            {
                "rowNumber": source["rowNumber"],
                "status": "ready" if not issues else "blocked",
                "issues": issues,
                "customer": (
                    {
                        "id": customer.id,
                        "slug": customer.slug,
                        "name": customer.name,
                    }
                    if customer is not None
                    else {
                        "selector": selector,
                        "matchCount": len(matches),
                    }
                ),
                "email": email,
                "currentAccount": (
                    {
                        "authUserId": str(current.auth_user_id),
                        "email": current.email,
                        "active": current.active,
                    }
                    if current is not None
                    else None
                ),
                "accountConflicts": email_conflicts,
                "agency": (
                    {
                        "id": agency.id,
                        "slug": agency.slug,
                        "name": agency.name,
                        "active": agency.active,
                    }
                    if agency is not None
                    else (
                        {
                            "id": None,
                            "slug": "",
                            "name": "Independent",
                            "active": True,
                        }
                        if independent
                        else {
                            "selector": agency_selector,
                            "matchCount": len(agency_matches),
                            "active": False,
                        }
                    )
                ),
                "membershipImpact": (
                    {
                        "currentAgencyId": customer.agency_id,
                        "destinationAgencyId": (
                            agency.id if agency is not None else None
                        ),
                        "willChange": customer.agency_id
                        != (agency.id if agency is not None else None),
                        "permanentHistoryWillMerge": (
                            agency is not None
                            and customer.agency_id != agency.id
                        ),
                    }
                    if customer is not None
                    else {}
                ),
                "historyCounts": counts,
            }
        )

    customer_counts = Counter(resolved_customers)
    duplicates = {
        customer_id
        for customer_id, count in customer_counts.items()
        if count > 1
    }
    if duplicates:
        for row in report_rows:
            customer = row["customer"]
            if isinstance(customer, dict) and customer.get("id") in duplicates:
                row["issues"].append(
                    {
                        "code": "duplicate_customer_mapping",
                        "detail": "The Customer appears more than once.",
                    }
                )
                row["status"] = "blocked"

    mapped = set(resolved_customers)
    unmapped = [
        {"id": customer.id, "slug": customer.slug, "name": customer.name}
        for customer in customers
        if customer.id not in mapped
    ]
    blockers = sum(len(row["issues"]) for row in report_rows) + len(unmapped)
    coverage = {
        "activeCustomers": len(customers),
        "mappedCustomers": len(mapped),
        "unmappedCustomers": unmapped,
    }
    plan = {
        "inputChecksum": _file_checksum(path),
        "coverage": coverage,
        "rows": report_rows,
    }
    return {
        "mode": "dry-run",
        "mutationPerformed": False,
        **plan,
        "planChecksum": _checksum(plan),
        "summary": {
            "valid": blockers == 0,
            "inputRows": len(source_rows),
            "readyRows": sum(
                row["status"] == "ready" for row in report_rows
            ),
            "blockers": blockers,
        },
    }


def _parse_timestamp(value: object, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise MigrationRefused(
            f"Backup receipt {field} must be an ISO-8601 timestamp."
        ) from None
    if parsed.tzinfo is None:
        raise MigrationRefused(
            f"Backup receipt {field} must include a timezone."
        )
    return parsed.astimezone(timezone.utc)


def validate_backup_receipt(
    path: Path,
    *,
    now: datetime | None = None,
) -> tuple[dict[str, object], str]:
    try:
        raw = path.read_bytes()
        receipt = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        raise MigrationRefused(
            "Verified-backup receipt must be readable JSON."
        ) from None
    if not isinstance(receipt, dict):
        raise MigrationRefused(
            "Verified-backup receipt must be a JSON object."
        )
    if not REQUIRED_BACKUP_FIELDS.issubset(receipt):
        missing = sorted(REQUIRED_BACKUP_FIELDS - set(receipt))
        raise MigrationRefused(
            "Verified-backup receipt is missing: " + ", ".join(missing)
        )
    dump_checksum = str(receipt["databaseDumpSha256"]).lower()
    if not _SHA256.fullmatch(dump_checksum):
        raise MigrationRefused(
            "Backup receipt databaseDumpSha256 must be a SHA-256 digest."
        )
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    verified_at = _parse_timestamp(receipt["verifiedAt"], "verifiedAt")
    restic_at = _parse_timestamp(
        receipt["resticCheckCompletedAt"],
        "resticCheckCompletedAt",
    )
    for field, timestamp in (
        ("verifiedAt", verified_at),
        ("resticCheckCompletedAt", restic_at),
    ):
        age = current - timestamp
        if age < timedelta(minutes=-5) or age > BACKUP_MAX_AGE:
            raise MigrationRefused(
                f"Backup receipt {field} must be within the last 24 hours."
            )
    for field in (
        "databaseSnapshot",
        "restoreRehearsalReference",
        "verifiedBy",
    ):
        if not str(receipt[field]).strip():
            raise MigrationRefused(f"Backup receipt {field} is required.")
    return receipt, hashlib.sha256(raw).hexdigest()


def _recover_or_invite(
    provider: MigrationIdentityProvider,
    mapping: UserAccountMigrationMapping,
) -> ProviderIdentity:
    candidates = [
        identity
        for identity in provider.list_users()
        if identity.email == mapping.email
    ]
    matching = [
        identity
        for identity in candidates
        if str(identity.metadata.get("jawnix_migration_run_id") or "")
        == str(mapping.run_id)
        and str(identity.metadata.get("jawnix_migration_mapping_id") or "")
        == str(mapping.id)
    ]
    if len(matching) == 1:
        return matching[0]
    if candidates:
        raise MigrationRefused(
            f"Identity conflict appeared for migration row "
            f"{mapping.row_number}."
        )
    return provider.invite(
        mapping.email,
        run_id=mapping.run_id,
        mapping_id=mapping.id,
    )


def _write_artifact(
    artifact_dir: Path,
    run_id: uuid.UUID,
    checksum: str,
    contents: dict[str, object],
) -> Path:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    path = artifact_dir / f"user-account-migration-{run_id}-{checksum}.json"
    payload = json.dumps(
        {"artifactSha256": checksum, "artifact": contents},
        indent=2,
        sort_keys=True,
    ).encode("utf-8") + b"\n"
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if (
            existing.get("artifactSha256") != checksum
            or _checksum(existing.get("artifact")) != checksum
        ):
            raise RuntimeError(
                "Existing reconciliation artifact failed checksum validation."
            )
        return path
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    try:
        with os.fdopen(descriptor, "wb") as target:
            target.write(payload)
            target.flush()
            os.fsync(target.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


def _artifact_contents(
    session: Session,
    run: UserAccountMigrationRun,
    mappings: list[UserAccountMigrationMapping],
) -> dict[str, object]:
    entries: list[dict[str, object]] = []
    for mapping in mappings:
        reconciled_history = {
            "customerDistributionEvents": int(
                session.scalar(
                    select(func.count(DistributionEvent.id)).where(
                        DistributionEvent.agent_id == mapping.customer_id
                    )
                )
                or 0
            ),
            "customerBatchRequests": int(
                session.scalar(
                    select(func.count(LeadRequest.id)).where(
                        LeadRequest.agent_id == mapping.customer_id
                    )
                )
                or 0
            ),
        }
        for key, actual in reconciled_history.items():
            if actual != mapping.history_counts[key]:
                raise MigrationRefused(
                    f"History count changed for Customer "
                    f"{mapping.customer_id}: {key}."
                )
        invitation = (
            session.get(UserAccountInvitation, mapping.invitation_id)
            if mapping.invitation_id is not None
            else None
        )
        prior = (
            session.get(UserAccount, mapping.prior_auth_user_id)
            if mapping.prior_auth_user_id is not None
            else None
        )
        if prior is None:
            deactivation = {
                "formerAuthUserId": None,
                "state": "not_required",
            }
        elif prior.active:
            deactivation = {
                "formerAuthUserId": str(prior.auth_user_id),
                "state": "deferred_until_acceptance",
                "formerAccountActive": True,
            }
        else:
            deactivation = {
                "formerAuthUserId": str(prior.auth_user_id),
                "state": "completed_after_acceptance",
                "formerAccountActive": False,
                "replacedByAuthUserId": (
                    str(prior.replaced_by_auth_user_id)
                    if prior.replaced_by_auth_user_id is not None
                    else None
                ),
            }
        entries.append(
            {
                "rowNumber": mapping.row_number,
                "mapping": {
                    "customerId": mapping.customer_id,
                    "customerSlug": mapping.customer_slug,
                    "email": mapping.email,
                    "agencyId": mapping.agency_id,
                    "agencySlug": mapping.agency_slug,
                },
                "invitation": {
                    "authUserId": str(mapping.invited_auth_user_id),
                    "invitationId": (
                        str(mapping.invitation_id)
                        if mapping.invitation_id is not None
                        else None
                    ),
                    "state": (
                        invitation.status
                        if invitation is not None
                        else "active_first_account"
                    ),
                },
                "deactivation": deactivation,
                "agencyResult": mapping.agency_result,
                "historyCounts": {
                    "planned": mapping.history_counts,
                    "reconciled": reconciled_history,
                },
                "identifiers": {
                    "customerIdPreserved": mapping.customer_id,
                    "distributionEventsRewritten": 0,
                },
                "attemptCount": mapping.attempt_count,
            }
        )
    return {
        "kind": "jawnix-user-account-migration-reconciliation",
        "version": 1,
        "runId": str(run.id),
        "inputChecksum": run.input_checksum,
        "approvedPlanChecksum": run.plan_checksum,
        "backup": {
            "receiptChecksum": run.backup_receipt_checksum,
            "databaseSnapshot": run.backup_snapshot,
            "receipts": list(run.backup_receipts),
        },
        "operator": run.operator,
        "reason": run.reason,
        "completedAt": run.completed_at.isoformat()
        if run.completed_at is not None
        else None,
        "mappings": entries,
    }


def _existing_artifact_result(
    session: Session,
    run: UserAccountMigrationRun,
    artifact_dir: Path,
) -> dict[str, object]:
    artifact = session.scalar(
        select(UserAccountMigrationArtifact).where(
            UserAccountMigrationArtifact.run_id == run.id
        )
    )
    if artifact is None:
        raise RuntimeError("Completed migration is missing reconciliation.")
    path = _write_artifact(
        artifact_dir,
        run.id,
        artifact.checksum,
        artifact.contents,
    )
    return {
        "mode": "apply",
        "runId": str(run.id),
        "status": "completed",
        "idempotentRerun": True,
        "artifactPath": str(path),
        "artifactSha256": artifact.checksum,
        "mappings": len(artifact.contents["mappings"]),
    }


def apply_user_account_migration(
    session_factory: Callable[[], Session],
    provider: MigrationIdentityProvider,
    settings: Settings,
    path: Path,
    *,
    approved_plan_checksum: str,
    backup_receipt_path: Path,
    artifact_dir: Path,
    operator: str,
    reason: str,
    confirmation: str,
) -> dict[str, object]:
    """Apply an approved plan, resuming safely after external interruption."""

    if confirmation != APPLY_CONFIRMATION:
        raise MigrationRefused(
            f"Apply confirmation must be exactly {APPLY_CONFIRMATION}."
        )
    if not operator.strip() or not reason.strip():
        raise MigrationRefused("Apply requires a named operator and reason.")
    receipt, receipt_checksum = validate_backup_receipt(backup_receipt_path)
    input_checksum = _file_checksum(path)

    with session_factory() as session:
        existing = session.scalar(
            select(UserAccountMigrationRun).where(
                UserAccountMigrationRun.input_checksum == input_checksum
            )
        )
        if existing is not None:
            if existing.plan_checksum != approved_plan_checksum:
                raise MigrationRefused(
                    "Existing migration journal has a different approved plan."
                )
            if existing.status == "completed":
                return _existing_artifact_result(
                    session,
                    existing,
                    artifact_dir,
                )
            seen_receipts = {
                str(item.get("receiptChecksum") or "")
                for item in existing.backup_receipts
            }
            if receipt_checksum not in seen_receipts:
                existing.backup_receipts = [
                    *existing.backup_receipts,
                    {
                        "receiptChecksum": receipt_checksum,
                        "databaseSnapshot": str(
                            receipt["databaseSnapshot"]
                        ).strip(),
                        "verifiedAt": str(receipt["verifiedAt"]),
                        "verifiedBy": str(receipt["verifiedBy"]).strip(),
                        "restoreRehearsalReference": str(
                            receipt["restoreRehearsalReference"]
                        ).strip(),
                    },
                ]
                record_activity(
                    session,
                    action="user_account_migration_backup_refreshed",
                    target_type="user_account_migration",
                    target_id=existing.id,
                    actor_id=operator,
                    reason=reason,
                    details={
                        "before": {
                            "backupReceiptChecksum": (
                                existing.backup_receipt_checksum
                            )
                        },
                        "after": {
                            "backupReceiptChecksum": receipt_checksum,
                            "databaseSnapshot": str(
                                receipt["databaseSnapshot"]
                            ).strip(),
                        },
                    },
                )
                session.commit()
            run_id = existing.id
        else:
            with session_factory() as preflight_session:
                report = dry_run_user_account_migration(
                    preflight_session,
                    provider,
                    path,
                )
            if not bool(report["summary"]["valid"]):
                raise MigrationRefused(
                    "Apply refused because the current dry-run contains blockers."
                )
            if approved_plan_checksum != report["planChecksum"]:
                raise MigrationRefused(
                    "Apply refused because the approved dry-run plan no longer "
                    "matches."
                )

            run = UserAccountMigrationRun(
                input_checksum=input_checksum,
                plan_checksum=approved_plan_checksum,
                status="in_progress",
                operator=operator.strip(),
                reason=reason.strip(),
                backup_receipt_checksum=receipt_checksum,
                backup_snapshot=str(receipt["databaseSnapshot"]).strip(),
                backup_receipts=[
                    {
                        "receiptChecksum": receipt_checksum,
                        "databaseSnapshot": str(
                            receipt["databaseSnapshot"]
                        ).strip(),
                        "verifiedAt": str(receipt["verifiedAt"]),
                        "verifiedBy": str(receipt["verifiedBy"]).strip(),
                        "restoreRehearsalReference": str(
                            receipt["restoreRehearsalReference"]
                        ).strip(),
                    }
                ],
            )
            session.add(run)
            session.flush()
            for row in report["rows"]:
                customer = row["customer"]
                agency = row["agency"]
                current = row["currentAccount"]
                session.add(
                    UserAccountMigrationMapping(
                        run_id=run.id,
                        row_number=int(row["rowNumber"]),
                        customer_id=int(customer["id"]),
                        customer_slug=str(customer["slug"]),
                        email=str(row["email"]),
                        agency_id=agency["id"],
                        agency_slug=str(agency["slug"]),
                        prior_auth_user_id=(
                            uuid.UUID(str(current["authUserId"]))
                            if current is not None
                            else None
                        ),
                        state="planned",
                        agency_before_id=row["membershipImpact"][
                            "currentAgencyId"
                        ],
                        history_counts=row["historyCounts"],
                    )
                )
            record_activity(
                session,
                action="user_account_migration_started",
                target_type="user_account_migration",
                target_id=run.id,
                actor_id=operator,
                reason=reason,
                details={
                    "before": None,
                    "after": {
                        "status": "in_progress",
                        "mappingCount": len(report["rows"]),
                        "planChecksum": approved_plan_checksum,
                        "backupReceiptChecksum": receipt_checksum,
                    },
                },
            )
            session.commit()
            run_id = run.id

    with session_factory() as session:
        mapping_ids = list(
            session.scalars(
                select(UserAccountMigrationMapping.id)
                .where(UserAccountMigrationMapping.run_id == run_id)
                .order_by(UserAccountMigrationMapping.row_number)
            )
        )

    for mapping_id in mapping_ids:
        with session_factory() as session:
            mapping = session.get(UserAccountMigrationMapping, mapping_id)
            if mapping is None:
                raise RuntimeError("Migration journal row disappeared.")
            if mapping.state in {"invited_pending", "active"}:
                continue
            mapping.state = "dispatching"
            mapping.attempt_count += 1
            mapping.last_error = ""
            session.commit()
            detached_mapping = mapping
        try:
            identity = _recover_or_invite(provider, detached_mapping)
            with session_factory() as session:
                mapping = session.scalar(
                    select(UserAccountMigrationMapping)
                    .where(UserAccountMigrationMapping.id == mapping_id)
                    .with_for_update()
                )
                customer = session.scalar(
                    select(Customer)
                    .where(Customer.id == mapping.customer_id)
                    .with_for_update()
                )
                agency = (
                    session.scalar(
                        select(Agency)
                        .where(Agency.id == mapping.agency_id)
                        .with_for_update()
                    )
                    if mapping.agency_id is not None
                    else None
                )
                if customer is None:
                    raise MigrationRefused("Mapped Customer disappeared.")
                if mapping.agency_id is not None and agency is None:
                    raise MigrationRefused("Destination Agency disappeared.")
                provision = invite_user_account(
                    session,
                    customer=customer,
                    auth_user_id=identity.id,
                    email=mapping.email,
                    actor_id=f"migration:{operator}",
                    reason=reason,
                )
                if customer.agency_id != mapping.agency_id:
                    counts = mapping.history_counts
                    assign_customer(
                        session,
                        customer=customer,
                        destination=agency,
                        actor_id=f"migration:{operator}",
                        reason=reason,
                        confirmed=True,
                        settings=settings,
                        precomputed_preview={
                            "consequences": {
                                "historyMergeIsPermanent": agency is not None,
                                "customerHistoryBlockedForDestination": counts[
                                    "permanentHistoryBefore"
                                ]["distributedLeads"],
                                "destinationHistoryBlockedForCustomer": counts[
                                    "destinationHistoryBefore"
                                ]["distributedLeads"],
                            }
                        },
                    )
                mapping.invited_auth_user_id = identity.id
                mapping.invitation_id = provision.invitation_id
                mapping.state = (
                    "invited_pending"
                    if provision.invitation_id is not None
                    else "active"
                )
                mapping.deactivation_state = (
                    "deferred_until_acceptance"
                    if provision.replaces_auth_user_id is not None
                    else "not_required"
                )
                mapping.agency_result = {
                    "beforeAgencyId": mapping.agency_before_id,
                    "afterAgencyId": mapping.agency_id,
                    "membershipChanged": (
                        mapping.agency_before_id != mapping.agency_id
                    ),
                    "permanentHistoryPreserved": True,
                }
                mapping.last_error = ""
                session.commit()
        except Exception as exc:
            with session_factory() as session:
                mapping = session.get(UserAccountMigrationMapping, mapping_id)
                if mapping is not None:
                    mapping.state = "failed"
                    mapping.last_error = str(exc)[:2000]
                    session.commit()
            raise

    with session_factory() as session:
        run = session.scalar(
            select(UserAccountMigrationRun)
            .where(UserAccountMigrationRun.id == run_id)
            .with_for_update()
        )
        mappings = list(
            session.scalars(
                select(UserAccountMigrationMapping)
                .where(UserAccountMigrationMapping.run_id == run_id)
                .order_by(UserAccountMigrationMapping.row_number)
            )
        )
        if any(
            mapping.state not in {"invited_pending", "active"}
            for mapping in mappings
        ):
            raise RuntimeError("Migration cannot reconcile incomplete rows.")
        run.status = "completed"
        run.completed_at = utcnow()
        contents = _artifact_contents(session, run, mappings)
        artifact_checksum = _checksum(contents)
        session.add(
            UserAccountMigrationArtifact(
                run_id=run.id,
                checksum=artifact_checksum,
                contents=contents,
            )
        )
        record_activity(
            session,
            action="user_account_migration_completed",
            target_type="user_account_migration",
            target_id=run.id,
            actor_id=operator,
            reason=reason,
            details={
                "before": {"status": "in_progress"},
                "after": {
                    "status": "completed",
                    "mappingCount": len(mappings),
                    "artifactChecksum": artifact_checksum,
                },
            },
        )
        session.commit()
        path = _write_artifact(
            artifact_dir,
            run.id,
            artifact_checksum,
            contents,
        )
        return {
            "mode": "apply",
            "runId": str(run.id),
            "status": "completed",
            "idempotentRerun": False,
            "artifactPath": str(path),
            "artifactSha256": artifact_checksum,
            "mappings": len(mappings),
        }



def draft_mapping_rows(session: Session) -> list[dict[str, str]]:
    """Every active Customer, with its current Agency already filled in.

    Authoring the mapping from scratch means hand-transcribing selectors that
    have to match `_matches_customer` and `_matches_agency` exactly — a slug
    typo becomes "No active Customer matches ...", discovered at dry-run.
    Reading the pairs out of the database instead leaves exactly one column to
    fill in by hand, and guarantees the selectors resolve.

    `migrate` is written empty on purpose. Rows are opt-in, so an untouched
    draft migrates nobody; deciding is a positive act rather than the default.
    """

    customers = list(
        session.scalars(
            select(Customer)
            .where(Customer.deleted_at.is_(None), Customer.active.is_(True))
            .order_by(Customer.id)
        )
    )
    agencies = {
        agency.id: agency
        for agency in session.scalars(
            select(Agency).where(Agency.deleted_at.is_(None))
        )
    }

    rows: list[dict[str, str]] = []
    for customer in customers:
        agency = agencies.get(customer.agency_id) if customer.agency_id else None
        rows.append(
            {
                "customer": customer.slug or str(customer.id),
                "email": "",
                # "independent" is the selector `_matches_agency` reads as
                # "no Agency", so an unaffiliated Customer round-trips.
                "agency": (agency.slug or str(agency.id)) if agency else "independent",
                "migrate": "",
                "customer_name": customer.name or "",
                "current_agency_name": agency.name if agency else "(independent)",
            }
        )
    return rows


def write_mapping_draft(session: Session, path: Path) -> dict[str, object]:
    """Write the draft CSV and report what it contains."""

    rows = draft_mapping_rows(session)
    if not rows:
        raise MigrationRefused("No active Customers to draft.")
    # customer_name and current_agency_name are for the human filling this in;
    # the loader ignores unknown columns.
    fieldnames = [
        "customer",
        "email",
        "agency",
        "migrate",
        "customer_name",
        "current_agency_name",
    ]
    with path.open("w", newline="", encoding="utf-8") as sink:
        writer = csv.DictWriter(sink, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return {
        "path": str(path),
        "customers": len(rows),
        "independent": sum(1 for row in rows if row["agency"] == "independent"),
        "note": "Fill in email, then set migrate=yes on the rows to migrate. "
        "Rows left blank are skipped.",
    }