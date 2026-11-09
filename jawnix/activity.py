from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterable, Mapping
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from urllib.parse import quote

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .models import (
    Agency,
    AuditEntry,
    Customer,
    CustomerProfile,
    CustomerRegistration,
    ExclusionList,
    Lead,
    LeadRequest,
    LeadUpload,
    ManagedAdmin,
    NightlyReview,
    ScrapeAnomaly,
    ScraperConfiguration,
    UserAccount,
)


_SECRET_KEY_PARTS = {
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "csrf",
    "handoff",
    "password",
    "refresh_token",
    "secret",
    "session",
    "signature",
    "token",
}


class UnsafeActivityDetailsError(ValueError):
    """Raised before secret-bearing details can reach an Audit Entry."""


ACTIVITY_PAGE_SIZE = 25
MAX_ACTIVITY_PAGE_SIZE = 100


_ENTITY_PATHS = {
    "agency": "/app/admin/agencies/{id}",
    "batch_request": "/app/admin/batches/{id}",
    "customer": "/app/admin/customers/{id}",
    "inventory_conflict": "/app/admin/fulfillment/conflicts/{id}",
    "lead_report": "/app/admin/fulfillment/reports/{id}",
    "scrape_run": "/app/admin/acquisition/runs/{id}",
    "scraper_configuration": "/app/admin/acquisition/configurations/{id}",
    "source_segment": "/app/admin/setup/niche-mappings?segment={id}",
}

_ENTITY_DESTINATIONS = {
    "administrator_mfa": "/app/admin/security",
    "dataset_publication": "/app/admin/insights#nightly-reviews",
    "exclusion_list": "/app/admin/inventory",
    "lead": "/app/admin/inventory",
    "lead_upload": "/app/admin/lead-uploads",
    "nightly_review": "/app/admin/insights#nightly-reviews",
    "scrape_anomaly": "/app/admin/today",
    "scraper_pipeline": "/app/admin/acquisition/scraper/workspace",
    "scraper_privileged_session": "/app/admin/acquisition/scraper",
    "scraper_keywords": (
        "/app/admin/acquisition/scraper/workspace/keywords"
    ),
    "scraper_keyword_generation": (
        "/app/admin/acquisition/scraper/workspace/keywords"
    ),
    "scraper_keyword_rollover": (
        "/app/admin/acquisition/scraper/workspace/keywords"
    ),
    "scraper_export": (
        "/app/admin/acquisition/scraper/workspace/database"
    ),
    "scraper_runtime_configuration": (
        "/app/admin/acquisition/scraper/workspace/runtime"
    ),
    "source_recommendation": "/app/admin/setup/configurations",
    "user_account": "/app/admin/customers",
}

#: What the filter calls each kind of record. Every type ever recorded is
#: offered (see ``activity_filters``); these are the readable names for the
#: ones Jawnix writes today, and anything new falls back to its words.
ENTITY_TYPE_LABELS = {
    "administrator_mfa": "Administrator security",
    "agency": "Agency",
    "batch_request": "Batch",
    "customer": "Customer",
    "dataset_publication": "Dataset publication",
    "exclusion_list": "Exclusion list",
    "inventory_conflict": "Inventory conflict",
    "lead": "Lead",
    "lead_report": "Lead report",
    "lead_upload": "DB2 upload",
    "listing_observation": "Listing observation",
    "managed_admin": "Managed administrator",
    "niche_assignment_upload": "Niche assignment upload",
    "nightly_review": "Nightly review",
    "scrape_anomaly": "Scrape anomaly",
    "scrape_run": "Scrape run",
    "scraper_configuration": "Scraper configuration",
    "scraper_export": "Scraper export",
    "scraper_keyword_generation": "Keyword generation",
    "scraper_keyword_rollover": "Keyword rollover",
    "scraper_keywords": "Scraper keywords",
    "scraper_pipeline": "Scraper pipeline",
    "scraper_privileged_session": "Scraper session",
    "scraper_runtime_configuration": "Scraper settings",
    "source_recommendation": "Source recommendation",
    "source_segment": "Niche mapping",
    "user_account": "User account",
    "user_account_migration": "User account migration",
}

#: Readable names for the actions whose words alone read badly. Everything
#: else is its own words with the record type's prefix tidied
#: (``_action_label``).
_ACTION_LABELS = {
    "admin_mfa_break_glass": "Break-glass recovery",
    "admin_mfa_break_glass_authorized": "Break-glass recovery authorized",
    "admin_mfa_break_glass_failed": "Break-glass recovery failed",
    "agency_deleted": "Agency deactivated and removed",
    "agency_hard_deleted": "Agency permanently deleted",
    "batch_artifact_downloaded": "Batch file downloaded",
    "batch_artifact_regenerated": "Batch file regenerated",
    "batch_request_notify": "Telegram re-notified",
    "batch_request_retry_delivery": "Batch delivery retried",
    "cooldown_window_updated": "Cooldown changed",
    "credit_purchase_settled_after_close": "Credit purchase settled late",
    "credit_wallet_adjusted": "Credit adjustment posted",
    "customer_agency_assignment_changed": "Agency assignment changed",
    "customer_billing_updated": "Billing configured",
    "customer_deleted": "Customer deactivated and removed",
    "customer_hard_deleted": "Customer permanently deleted",
    "customer_inventory_scope_changed": "Lead database changed",
    "customer_personal_data_erased": "Personal data erased",
    "customer_registration_decided": "Registration decided",
    "customer_registration_review_requested": "Registration needs review",
    "customer_registration_states_selected": "Registration states chosen",
    "customer_user_account_provisioned": "User account invited",
    "customer_user_account_replaced": "User account replaced",
    "eligibility_hold_applied": "Eligibility hold applied",
    "eligibility_hold_removed": "Eligibility hold removed",
    "exclusion_list_ingested": "Exclusion list processed",
    "licensed_states_update": "Appointed states changed",
    "listing_observation_conflicts_correction": "Listing conflicts with a correction",
    "manager_lead_upload_published": "DB2 upload published",
    "manager_lead_upload_submitted": "DB2 upload submitted",
    "niche_policy_updated": "Niche policy changed",
    "operator_lead_upload_submitted": "DB2 upload submitted",
    "scraper_configuration_baseline_imported": "Configuration baseline imported",
    "scraper_manual_run_queued": "Manual scrape run queued",
    "scraper_step_up_succeeded": "Scraper workspace verified",
    "scraper_step_up_unavailable": "Scraper verification unavailable",
    "source_niche_confirmed": "Niche mapping confirmed",
    "source_niche_denied": "Niche mapping denied",
    "user_account_customer_mapped": "User account linked to Customer",
    "user_account_password_reset_sent": "Password reset sent",
    "user_accounts_synchronized": "User accounts synchronized",
}

#: Record-type prefixes an action name repeats, and the word a label uses
#: for them instead ("batch_request_approve" -> "Batch approve").
_ACTION_PREFIXES = (
    ("batch_request_", "Batch"),
    ("customer_", "Customer"),
    ("agency_", "Agency"),
    ("lead_report_", "Lead report"),
    ("inventory_conflict_", "Conflict"),
    ("scrape_anomaly_", "Scrape anomaly"),
    ("exclusion_list_", "Exclusion list"),
    ("source_recommendation_", "Source recommendation"),
    ("scraper_configuration_", "Configuration"),
    ("scraper_runtime_configuration_", "Scraper settings"),
    ("scraper_keywords_", "Keywords"),
    ("scraper_keyword_", "Keyword"),
    ("scraper_", "Scraper"),
    ("admin_mfa_", "Authenticator"),
    ("user_account_", "User account"),
    ("lead_", "Lead"),
)

#: Past tense for the verbs transition actions are named with.
_PAST = {
    "approve": "approved",
    "reject": "rejected",
    "cancel": "canceled",
    "retry": "retried",
    "dismiss": "dismissed",
    "correct": "corrected",
    "suppress": "suppressed",
    "confirm": "confirmed",
    "deny": "denied",
}

REFUSED_SUFFIX = "_refused"

#: What a refusal refused, keyed by the action with its suffix removed.
_REFUSED_LABELS = {
    "agency_hard_delete": "permanent deletion",
    "customer_hard_delete": "permanent deletion",
    "scraper_keywords_save": "keyword save",
    "scraper_runtime_configuration_save": "scraper settings save",
}


def _normalized_key(key: object) -> str:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(key))
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


def _assert_safe_detail_keys(value: object, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, Mapping):
        for raw_key, nested in value.items():
            key = _normalized_key(raw_key)
            if any(part in key for part in _SECRET_KEY_PARTS):
                location = ".".join((*path, str(raw_key)))
                raise UnsafeActivityDetailsError(
                    f"Activity details contain a secret-bearing key: {location}"
                )
            _assert_safe_detail_keys(nested, (*path, str(raw_key)))
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            _assert_safe_detail_keys(nested, (*path, str(index)))


def _contains_secret(value: object, secret: str) -> bool:
    if isinstance(value, str):
        return secret in value
    if isinstance(value, Mapping):
        return any(
            _contains_secret(key, secret)
            or _contains_secret(nested, secret)
            for key, nested in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_secret(nested, secret) for nested in value)
    return False


def _activity_entity_href(target_type: str, target_id: str) -> str:
    if path := _ENTITY_PATHS.get(target_type):
        return path.format(id=quote(target_id, safe=""))
    if destination := _ENTITY_DESTINATIONS.get(target_type):
        return destination
    return (
        "/app/admin/activity?"
        f"entityType={quote(target_type, safe='')}&"
        f"entityId={quote(target_id, safe='')}"
    )


def _words(value: str) -> str:
    text = value.replace("_", " ").strip()
    return text[:1].upper() + text[1:] if text else "Unknown"


def entity_type_label(target_type: str) -> str:
    return ENTITY_TYPE_LABELS.get(target_type) or _words(target_type)


def is_refused(action: str) -> bool:
    return action.endswith(REFUSED_SUFFIX)


def action_label(action: str) -> str:
    """The Activity wording for one recorded action ("Batch approved")."""
    if is_refused(action):
        refused = action[: -len(REFUSED_SUFFIX)]
        return f"Refused: {_REFUSED_LABELS.get(refused) or _action_rest(refused).lower()}"
    if label := _ACTION_LABELS.get(action):
        return label
    for prefix, noun in _ACTION_PREFIXES:
        if action.startswith(prefix) and len(action) > len(prefix):
            rest = action[len(prefix):]
            return f"{noun} {_PAST.get(rest, rest.replace('_', ' '))}"
    return _words(action)


def _action_rest(action: str) -> str:
    for prefix, _ in _ACTION_PREFIXES:
        if action.startswith(prefix) and len(action) > len(prefix):
            return action[len(prefix):].replace("_", " ")
    return action.replace("_", " ")


def _uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value).removeprefix("user_account:"))
    except (TypeError, ValueError):
        return None


def _integer(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _person(first: str, last: str) -> str:
    return " ".join(part for part in (first.strip(), last.strip()) if part)


class _Names:
    """Display names for one page of Audit Entries, read in a few queries.

    Actors are stored as identifiers (a provider user id, ``system:…``,
    ``telegram:…``, ``user_account:…``); records as a type and id. Activity
    shows people and records by name, so each page resolves the ones it holds
    together rather than one query per row.
    """

    def __init__(self, session: Session, entries: list[AuditEntry]) -> None:
        self.session = session
        ids: dict[str, set[str]] = {}
        for entry in entries:
            ids.setdefault(entry.target_type, set()).add(entry.target_id)
        self.people = _people(
            session,
            {entry.actor_user_id for entry in entries}
            | ids.get("user_account", set()),
        )
        self.records = _records(session, ids, self.people)

    def actor(self, actor: str) -> str:
        return actor_name(actor, self.people)

    def record(self, target_type: str, target_id: str) -> dict[str, str]:
        return self.records.get(
            (target_type, target_id),
            {
                "label": _default_record_label(target_type, target_id),
                "href": _activity_entity_href(target_type, target_id),
            },
        )


def _people(session: Session, actors: set[str]) -> dict[str, dict[str, object]]:
    """Customer users, registrations, and managed administrators by id."""
    wanted: dict[uuid.UUID, str] = {}
    for actor in actors:
        if actor.startswith(("system:", "telegram:")):
            continue
        parsed = _uuid(actor)
        if parsed is not None:
            wanted[parsed] = actor
    if not wanted:
        return {}
    found: dict[str, dict[str, object]] = {}
    keys = list(wanted)
    for profile in session.scalars(
        select(CustomerProfile).where(CustomerProfile.user_id.in_(keys))
    ):
        name = _person(profile.first_name, profile.last_name)
        found[str(profile.user_id)] = {
            "name": name or profile.email,
            "customerId": profile.agent_id,
            "kind": "customer",
        }
    for registration in session.scalars(
        select(CustomerRegistration).where(
            CustomerRegistration.auth_user_id.in_(keys)
        )
    ):
        found.setdefault(
            str(registration.auth_user_id),
            {
                "name": _person(registration.first_name, registration.last_name)
                or registration.email,
                "customerId": registration.customer_id,
                "kind": "customer",
            },
        )
    accounts = list(
        session.scalars(
            select(UserAccount).where(UserAccount.auth_user_id.in_(keys))
        )
    )
    customers = {
        customer.id: customer.name
        for customer in session.scalars(
            select(Customer).where(
                Customer.id.in_(
                    {
                        account.customer_id
                        for account in accounts
                        if account.customer_id is not None
                    }
                )
            )
        )
    }
    for account in accounts:
        key = str(account.auth_user_id)
        person = found.setdefault(
            key,
            {
                "name": customers.get(account.customer_id or -1) or account.email,
                "customerId": account.customer_id,
                "kind": "customer",
            },
        )
        if person.get("customerId") is None:
            person["customerId"] = account.customer_id
    for manager in session.scalars(
        select(ManagedAdmin).where(ManagedAdmin.auth_user_id.in_(keys))
    ):
        found.setdefault(
            str(manager.auth_user_id),
            {"name": manager.name, "customerId": None, "kind": "manager"},
        )
    return {
        actor: found[str(parsed)]
        for parsed, actor in wanted.items()
        if str(parsed) in found
    }


def actor_people(
    session: Session, actors: Iterable[str]
) -> dict[str, dict[str, object]]:
    """Resolve actor identifiers for ``actor_name`` outside an Activity page."""
    return _people(session, {actor for actor in actors if actor})


def actor_kind(actor: str, people: dict[str, dict[str, object]]) -> str:
    """``system``, ``customer``, ``manager``, or ``operator``.

    The operator is the one administrator, so an actor that is neither the
    system nor a known Customer user or managed administrator is them —
    including Telegram approvals, which act with the operator's authority.
    """
    if actor.startswith("system:"):
        return "system"
    if person := people.get(actor):
        return str(person["kind"])
    if actor.startswith("user_account:"):
        return "customer"
    return "operator"


def actor_name(actor: str, people: dict[str, dict[str, object]]) -> str:
    kind = actor_kind(actor, people)
    if kind == "system":
        return "System"
    if person := people.get(actor):
        return str(person["name"])
    if kind == "customer":
        return "Customer"
    return "Operator"


def _default_record_label(target_type: str, target_id: str) -> str:
    match target_type:
        case "scrape_run":
            return f"Run {target_id}"
        case "source_segment":
            state, separator, keyword = target_id.partition("::")
            return f"{keyword} · {state}" if separator else target_id
        case "lead_report" | "inventory_conflict" | "source_recommendation":
            return entity_type_label(target_type)
        case "administrator_mfa" | "scraper_privileged_session":
            return entity_type_label(target_type)
        case _ if target_type.startswith("scraper_"):
            return entity_type_label(target_type)
        case _:
            return f"{entity_type_label(target_type)} {target_id}"


def _records(
    session: Session,
    ids: dict[str, set[str]],
    people: dict[str, dict[str, object]],
) -> dict[tuple[str, str], dict[str, str]]:
    """Names (and, where it is better, links) for the records on one page."""
    records: dict[tuple[str, str], dict[str, str]] = {}

    def by_uuid(target_type: str) -> dict[uuid.UUID, str]:
        return {
            parsed: value
            for value in ids.get(target_type, set())
            if (parsed := _uuid(value)) is not None
        }

    def by_int(target_type: str) -> dict[int, str]:
        return {
            parsed: value
            for value in ids.get(target_type, set())
            if (parsed := _integer(value)) is not None
        }

    def put(target_type: str, target_id: str, label: str, href: str | None = None):
        records[(target_type, target_id)] = {
            "label": label,
            "href": href or _activity_entity_href(target_type, target_id),
        }

    if wanted := by_uuid("batch_request"):
        for item_id, number in session.execute(
            select(LeadRequest.id, LeadRequest.number).where(
                LeadRequest.id.in_(list(wanted))
            )
        ):
            if number is not None:
                put(
                    "batch_request",
                    wanted[item_id],
                    f"Batch #{number}",
                    f"/app/admin/batches/{number}",
                )
    customer_ids = by_int("customer")
    if customer_ids:
        for customer_id, name in session.execute(
            select(Customer.id, Customer.name).where(
                Customer.id.in_(list(customer_ids))
            )
        ):
            put("customer", customer_ids[customer_id], name)
    if wanted := by_int("agency"):
        for agency_id, name in session.execute(
            select(Agency.id, Agency.name).where(Agency.id.in_(list(wanted)))
        ):
            put("agency", wanted[agency_id], name)
    if wanted := by_uuid("scraper_configuration"):
        for item_id, version in session.execute(
            select(ScraperConfiguration.id, ScraperConfiguration.version).where(
                ScraperConfiguration.id.in_(list(wanted))
            )
        ):
            put("scraper_configuration", wanted[item_id], f"Configuration v{version}")
    if wanted := by_uuid("nightly_review"):
        for item_id, review_date in session.execute(
            select(NightlyReview.id, NightlyReview.review_date).where(
                NightlyReview.id.in_(list(wanted))
            )
        ):
            if review_date is not None:
                put(
                    "nightly_review",
                    wanted[item_id],
                    f"Nightly review {review_date.strftime('%b')} {review_date.day}",
                )
    if wanted := by_uuid("scrape_anomaly"):
        for item_id, run_id in session.execute(
            select(ScrapeAnomaly.id, ScrapeAnomaly.scraper_run_id).where(
                ScrapeAnomaly.id.in_(list(wanted))
            )
        ):
            put(
                "scrape_anomaly",
                wanted[item_id],
                f"Run {run_id} anomaly",
                f"/app/admin/acquisition/runs/{run_id}",
            )
    for target_type, model in (
        ("lead_upload", LeadUpload),
        ("exclusion_list", ExclusionList),
    ):
        if wanted := by_uuid(target_type):
            for item_id, filename in session.execute(
                select(model.id, model.filename).where(
                    model.id.in_(list(wanted))
                )
            ):
                put(target_type, wanted[item_id], filename)
    if wanted := by_int("lead"):
        for lead_id, phone in session.execute(
            select(Lead.id, Lead.phone).where(Lead.id.in_(list(wanted)))
        ):
            put("lead", wanted[lead_id], f"Lead {phone}")
    for value in ids.get("user_account", set()):
        person = people.get(value)
        if person is None:
            continue
        customer_id = person.get("customerId")
        put(
            "user_account",
            value,
            str(person["name"]),
            f"/app/admin/customers/{customer_id}" if customer_id else None,
        )
    return records


def _activity_entry(entry: AuditEntry, names: _Names) -> dict[str, object]:
    record = names.record(entry.target_type, entry.target_id)
    return {
        "id": str(entry.id),
        "action": entry.action,
        "actionLabel": action_label(entry.action),
        "refused": is_refused(entry.action),
        "entityType": entry.target_type,
        "entityTypeLabel": entity_type_label(entry.target_type),
        "entityId": entry.target_id,
        "entityLabel": record["label"],
        "entityHref": record["href"],
        "actor": entry.actor_user_id,
        "actorName": names.actor(entry.actor_user_id),
        "reason": entry.reason,
        "details": entry.details or {},
        "recordedAt": entry.created_at,
    }


#: The "Who" choices that group many actor identifiers.
WHO_SYSTEM = "system"
WHO_OPERATOR = "operator"


def _actor_ids(session: Session) -> list[str]:
    return [
        value
        for value in session.scalars(
            select(AuditEntry.actor_user_id).distinct()
        )
        if value
    ]


def _who_filter(session: Session, who: str):
    """The actor condition for one "Who" choice, or None for anyone."""
    if who == WHO_SYSTEM:
        return AuditEntry.actor_user_id.startswith("system:", autoescape=True)
    actors = _actor_ids(session)
    if who == WHO_OPERATOR:
        people = _people(session, set(actors))
        operators = [
            actor
            for actor in actors
            if actor_kind(actor, people) == "operator"
        ]
        return AuditEntry.actor_user_id.in_(operators)
    return AuditEntry.actor_user_id == who


def activity_filters(session: Session) -> dict[str, object]:
    """The choices the Activity filters offer: every recorded record type,
    and everyone who has acted (the operator and the system as one choice
    each, Customer users by name).

    Both read distinct values of an indexed column; Activity grows by a few
    entries a day, so the lists stay short.
    """
    types = sorted(
        {
            value
            for value in session.scalars(
                select(AuditEntry.target_type).distinct()
            )
            if value
        }
        | set(ENTITY_TYPE_LABELS),
        key=lambda value: entity_type_label(value).casefold(),
    )
    actors = _actor_ids(session)
    people = _people(session, set(actors))
    named = sorted(
        {
            actor: actor_name(actor, people)
            for actor in actors
            if actor_kind(actor, people) in {"customer", "manager"}
        }.items(),
        key=lambda item: (item[1].casefold(), item[0]),
    )
    return {
        "entityTypes": [
            {"value": value, "label": entity_type_label(value)}
            for value in types
        ],
        "who": [
            {"value": WHO_OPERATOR, "label": "Operator"},
            {"value": WHO_SYSTEM, "label": "System"},
            *({"value": actor, "label": name} for actor, name in named),
        ],
    }


def query_activity(
    session: Session,
    *,
    actor: str | None = None,
    action: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = 1,
    page_size: int = ACTIVITY_PAGE_SIZE,
    query: str | None = None,
    who: str | None = None,
    refused: bool = False,
) -> dict[str, object]:
    """Read global Activity and entity timelines through one query seam.

    ``actor`` matches one stored identifier exactly. ``who`` is the filter's
    grouping of actors: ``operator``, ``system``, or one identifier.
    ``refused`` keeps only guarded actions that were refused.
    """

    normalized_page = max(1, page)
    normalized_page_size = min(
        MAX_ACTIVITY_PAGE_SIZE,
        max(1, page_size),
    )
    filters = []
    if query and query.strip():
        term = f"%{query.strip()}%"
        filters.append(
            or_(
                AuditEntry.action.ilike(term),
                AuditEntry.target_type.ilike(term),
                AuditEntry.target_id.ilike(term),
                AuditEntry.actor_user_id.ilike(term),
                AuditEntry.reason.ilike(term),
            )
        )
    if actor and actor.strip():
        filters.append(AuditEntry.actor_user_id == actor.strip())
    if who and who.strip():
        filters.append(_who_filter(session, who.strip()))
    if refused:
        filters.append(
            AuditEntry.action.endswith(REFUSED_SUFFIX, autoescape=True)
        )
    if action and action.strip():
        filters.append(AuditEntry.action == action.strip())
    if entity_type and entity_type.strip():
        filters.append(AuditEntry.target_type == entity_type.strip())
    if entity_id and entity_id.strip():
        filters.append(AuditEntry.target_id == entity_id.strip())
    if date_from is not None:
        filters.append(
            AuditEntry.created_at
            >= datetime.combine(date_from, time.min, tzinfo=timezone.utc)
        )
    if date_to is not None:
        filters.append(
            AuditEntry.created_at
            < datetime.combine(
                date_to + timedelta(days=1),
                time.min,
                tzinfo=timezone.utc,
            )
        )

    total = session.scalar(
        select(func.count()).select_from(AuditEntry).where(*filters)
    ) or 0
    pages = max(1, (total + normalized_page_size - 1) // normalized_page_size)
    bounded_page = min(normalized_page, pages)
    entries = list(
        session.scalars(
            select(AuditEntry)
            .where(*filters)
            .order_by(AuditEntry.created_at.desc(), AuditEntry.id.desc())
            .offset((bounded_page - 1) * normalized_page_size)
            .limit(normalized_page_size)
        )
    )
    names = _Names(session, entries)
    return {
        "entries": [_activity_entry(entry, names) for entry in entries],
        "page": bounded_page,
        "pageSize": normalized_page_size,
        "total": total,
        "pages": pages,
    }


def record_activity(
    session: Session,
    *,
    action: str,
    target_type: str,
    target_id: object,
    actor_id: object,
    reason: str,
    details: Mapping[str, Any] | None = None,
    known_secrets: Iterable[str] = (),
) -> AuditEntry:
    """Record one consequential durable change through the shared write seam.

    Consequential actions are durable state transitions, eligibility or
    suppression changes, identity or membership changes, configuration
    activation, and destructive or irreversible operations. Ordinary reads,
    transport bookkeeping, and idempotent no-ops do not belong here. Retrieval
    of a retained Batch Artifact is the deliberate exception: the file is
    sensitive Customer data and ADR 0015 requires immutable download evidence.

    The caller supplies a deliberately small, safe before/after summary. A
    reason is always required, system work uses a stable ``system:`` actor,
    and secret-bearing keys or known secret material are rejected before an
    Audit Entry is added. The helper deliberately does not commit: successful
    actions and their evidence must commit atomically. For a refused action,
    callers must first roll back the failed transaction, then call this helper
    and commit the evidence in a fresh transaction.

    The deliberate exception is break-glass MFA recovery
    (``jawnix.break_glass``), which must commit authorization and failure
    evidence at mid-flow checkpoints before and after the external provider
    call and therefore constructs ``AuditEntry`` rows directly.
    """
    normalized_action = action.strip()
    normalized_target_type = target_type.strip()
    normalized_actor = str(actor_id).strip()
    normalized_reason = reason.strip()
    safe_details = dict(details or {})
    if not normalized_action:
        raise ValueError("Activity action is required.")
    if not normalized_target_type:
        raise ValueError("Activity target type is required.")
    if not normalized_actor:
        raise ValueError("Activity actor is required.")
    if not normalized_reason:
        raise ValueError("Activity reason is required.")
    _assert_safe_detail_keys(safe_details)
    try:
        json.dumps(
            safe_details,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Activity details must be JSON-safe.") from exc
    for secret in known_secrets:
        if secret and (
            _contains_secret(
                {
                    "action": normalized_action,
                    "targetType": normalized_target_type,
                    "targetId": str(target_id),
                    "actorId": normalized_actor,
                    "reason": normalized_reason,
                    "details": safe_details,
                },
                secret,
            )
        ):
            raise UnsafeActivityDetailsError(
                "Activity details contain known secret material."
            )
    entry = AuditEntry(
        action=normalized_action,
        target_type=normalized_target_type,
        target_id=str(target_id),
        actor_user_id=normalized_actor,
        reason=normalized_reason,
        details=safe_details,
    )
    session.add(entry)
    return entry
