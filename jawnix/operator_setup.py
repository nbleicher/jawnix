"""Setup: the operator's occasional tools, each with its state and last change.

Daily decisions live on Today. Setup is the index of everything changed only
now and then — the Scraper workspace and its settings, Scraper Configuration
versions, keywords, Niche mappings, Agencies, administrator security, and
billing defaults — so each row says where that tool stands and when it last
changed, from Jawnix's own records.

Two rows need what only the browser can ask for: whether this sign-in has a
live Scraper grant (its cookie is scoped to the Scraper API) and the
authenticator list (held by the identity provider). The page adds those; the
rows here carry everything Jawnix stores.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import Settings
from .models import (
    Agency,
    AgencyMembershipHistory,
    AuditEntry,
    Customer,
    KeywordCandidatePool,
    ScraperConfiguration,
    ScraperRun,
    ScraperRuntimeConfigurationRevision,
    SourceNicheMapping,
    SourceSegment,
)


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _latest(*values: datetime | None) -> datetime | None:
    present = [_utc(value) for value in values if value is not None]
    return max(present) if present else None


def _area(
    key: str,
    label: str,
    href: str,
    state: str,
    *,
    changed_at: datetime | None = None,
    changed_note: str | None = None,
    attention: bool = False,
    upcoming: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "key": key,
        "label": label,
        "href": href,
        "state": state,
        # Red text is reserved for something that needs the operator.
        "attention": attention,
        "changedAt": _utc(changed_at),
        "changedNote": changed_note,
        "upcoming": upcoming,
    }


def _last_activity(db: Session, *target_types: str, exclude: str = "") -> datetime | None:
    statement = select(func.max(AuditEntry.created_at)).where(
        AuditEntry.target_type.in_(target_types)
    )
    if exclude:
        statement = statement.where(AuditEntry.action.not_like(f"%{exclude}%"))
    return db.scalar(statement)


def _plural(count: int, one: str, many: str | None = None) -> str:
    return f"{count:,} {one if count == 1 else (many or one + 's')}"


def _words(value: str) -> str:
    return value.replace("_", " ")


def _scraper_workspace(db: Session) -> dict[str, object]:
    run = db.scalar(
        select(ScraperRun).order_by(ScraperRun.started_at.desc(), ScraperRun.id.desc()).limit(1)
    )
    state = (
        f"Run {run.id} {_words(run.status)}" if run is not None else "No Scrape Run yet"
    )
    return _area(
        "scraperWorkspace",
        "Scraper workspace",
        "/app/admin/acquisition/scraper",
        state,
        changed_at=(run.finished_at or run.started_at) if run is not None else None,
    )


def _scraper_settings(db: Session) -> dict[str, object]:
    revision = db.scalar(
        select(ScraperRuntimeConfigurationRevision)
        .order_by(ScraperRuntimeConfigurationRevision.created_at.desc())
        .limit(1)
    )
    if revision is None:
        return _area(
            "scraperSettings",
            "Scraper settings",
            "/app/admin/acquisition/scraper/workspace/runtime",
            "Not changed from Jawnix yet",
        )
    configuration = revision.configuration or {}
    states = configuration.get("activeStates") or []
    queue = configuration.get("queue") or {}
    parts = [_plural(len(states), "active state")]
    if isinstance(queue.get("target_depth"), int):
        parts.append(f"queue depth {queue['target_depth']:,}")
    return _area(
        "scraperSettings",
        "Scraper settings",
        "/app/admin/acquisition/scraper/workspace/runtime",
        " · ".join(parts),
        changed_at=revision.created_at,
    )


def _configurations(db: Session) -> dict[str, object]:
    versions = list(
        db.scalars(
            select(ScraperConfiguration).order_by(ScraperConfiguration.version.desc())
        )
    )
    active = next((item for item in versions if item.status == "active"), None)
    scheduled = next((item for item in versions if item.status == "scheduled"), None)
    parts = []
    if active is not None:
        segments = int(
            db.scalar(
                select(func.count(SourceSegment.id)).where(
                    SourceSegment.configuration_id == active.id
                )
            )
            or 0
        )
        parts.append(f"v{active.version} active · {_plural(segments, 'segment')}")
    if scheduled is not None:
        parts.append(f"v{scheduled.version} scheduled")
    return _area(
        "configurations",
        "Configuration versions",
        "/app/admin/setup/configurations",
        " · ".join(parts) or "No version yet",
        changed_at=_latest(
            *(
                value
                for item in versions[:3]
                for value in (item.created_at, item.scheduled_at, item.activated_at)
            )
        ),
    )


def _keywords(db: Session) -> dict[str, object]:
    pool = db.get(KeywordCandidatePool, 1)
    candidates = len(pool.candidates or []) if pool is not None else 0
    next_refill = _utc(pool.next_refill_at) if pool is not None else None
    return _area(
        "keywords",
        "Keywords",
        "/app/admin/acquisition/scraper/workspace/keywords",
        f"{_plural(candidates, 'keyword')} in reserve",
        changed_at=_last_activity(
            db,
            "scraper_keywords",
            "scraper_keyword_generation",
            "scraper_keyword_rollover",
        ),
        upcoming=(
            {"label": "next refill", "at": next_refill}
            if next_refill is not None
            else None
        ),
    )


def _niche_mappings(db: Session) -> dict[str, object]:
    pending, confirmed, denied, changed = db.execute(
        select(
            func.count(SourceNicheMapping.id).filter(
                SourceNicheMapping.confirmed.is_(False),
                SourceNicheMapping.denied_at.is_(None),
            ),
            func.count(SourceNicheMapping.id).filter(
                SourceNicheMapping.confirmed.is_(True)
            ),
            func.count(SourceNicheMapping.id).filter(
                SourceNicheMapping.denied_at.is_not(None)
            ),
            func.max(SourceNicheMapping.updated_at),
        )
    ).one()
    pending, confirmed, denied = int(pending or 0), int(confirmed or 0), int(denied or 0)
    if pending:
        state = f"{pending:,} awaiting review"
    elif confirmed or denied:
        state = f"{confirmed:,} confirmed" + (f" · {denied:,} denied" if denied else "")
    else:
        state = "No mappings yet"
    return _area(
        "nicheMappings",
        "Niche mappings",
        "/app/admin/setup/niche-mappings",
        state,
        changed_at=changed,
        attention=pending > 0,
    )


def _agencies(db: Session) -> dict[str, object]:
    active = int(
        db.scalar(
            select(func.count(Agency.id)).where(
                Agency.deleted_at.is_(None), Agency.active.is_(True)
            )
        )
        or 0
    )
    members = int(
        db.scalar(
            select(func.count(Customer.id))
            .join(Agency, Agency.id == Customer.agency_id)
            .where(Customer.deleted_at.is_(None), Agency.deleted_at.is_(None))
        )
        or 0
    )
    return _area(
        "agencies",
        "Agencies",
        "/app/admin/agencies",
        f"{active:,} active · {_plural(members, 'member')}",
        changed_at=_latest(
            _last_activity(db, "agency"),
            db.scalar(select(func.max(AgencyMembershipHistory.started_at))),
        ),
    )


def _security(db: Session) -> dict[str, object]:
    # Challenges are sign-ins, not changes to the factors themselves.
    return _area(
        "security",
        "Security",
        "/app/admin/security",
        "Authenticator required at every sign-in",
        changed_at=_last_activity(db, "administrator_mfa", exclude="challenge"),
    )


def _billing(db: Session, settings: Settings) -> tuple[dict[str, object], dict[str, object]]:
    rate = settings.default_lead_rate_cents_per_thousand
    billed = int(
        db.scalar(
            select(func.count(Customer.id)).where(
                Customer.deleted_at.is_(None),
                Customer.billing_enabled.is_(True),
            )
        )
        or 0
    )
    parts = [
        f"Starting rate ${rate / 100:,.2f} per 1,000"
        if rate is not None
        else "No starting rate"
    ]
    if not settings.billing_enabled:
        parts.append("platform billing off")
    details = {
        "enabled": settings.billing_enabled,
        "startingRateCentsPerThousand": rate,
        "billedCustomers": billed,
    }
    return (
        _area(
            "billing",
            "Billing defaults",
            "/app/admin/setup?area=billing",
            " · ".join(parts),
            changed_note="Server setting",
        ),
        details,
    )


def operator_setup(
    db: Session,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    """The Setup index: one row per occasional tool, in the order of use."""
    billing_area, billing = _billing(db, settings)
    return {
        "generatedAt": _utc(now) or datetime.now(timezone.utc),
        "areas": [
            _scraper_workspace(db),
            _scraper_settings(db),
            _configurations(db),
            _keywords(db),
            _niche_mappings(db),
            _agencies(db),
            _security(db),
            billing_area,
        ],
        "billing": billing,
    }
