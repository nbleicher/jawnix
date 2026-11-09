"""Usable DB1 and DB2 inventory totals, refreshed by a background job.

Usable means not suppressed, no active Eligibility Hold, and not globally
excluded: the same rule for both Lead Databases (see ``_actively_held`` and
``_globally_excluded`` in ``pool_analytics``). Per-Customer rules (Licensed
States, Niche Policy, no-repeat, Cooldown Window) are not applied here.

Each database is counted in one aggregate pass (``COUNT ... FILTER``) on the
maintenance worker lane. Request handlers only read the stored rows and their
as-of time, and only enqueue a refresh.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import and_, func, select, text
from sqlalchemy.orm import Session

from .jobs import enqueue_job
from .lead_databases import inventory_scope, inventory_state
from .models import (
    EligibilityHold,
    ExclusionList,
    ExclusionPhone,
    InventorySnapshot,
    Job,
    JobStatus,
    Lead,
    utcnow,
)


log = logging.getLogger(__name__)

REFRESH_INVENTORY_JOB = "refresh_inventory_snapshots"
DATABASES = ("db1", "db2")
DATABASE_LABELS = {"db1": "DB1", "db2": "DB2"}


def _as_of_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _aggregate(database: str):
    """One pass over a database's rows, bucketed by state.

    Holds and global exclusions are outer-joined as distinct key sets rather
    than tested with a correlated EXISTS per row, so PostgreSQL can hash them
    once instead of probing an index nine million times.
    """
    held = (
        select(EligibilityHold.lead_id)
        .where(EligibilityHold.active.is_(True))
        .distinct()
        .subquery("held_leads")
    )
    excluded = (
        select(ExclusionPhone.phone)
        .join(
            ExclusionList,
            ExclusionList.id == ExclusionPhone.exclusion_list_id,
        )
        .where(ExclusionList.global_effective.is_(True))
        .distinct()
        .subquery("excluded_phones")
    )
    suppressed = Lead.suppressed.is_(True)
    unsuppressed = Lead.suppressed.is_(False)
    is_held = held.c.lead_id.is_not(None)
    not_held = held.c.lead_id.is_(None)
    is_excluded = excluded.c.phone.is_not(None)
    not_excluded = excluded.c.phone.is_(None)
    state = inventory_state(database)
    query = inventory_scope(
        select(
            state.label("state"),
            func.count().label("all_rows"),
            func.count().filter(suppressed).label("suppressed"),
            func.count().filter(and_(unsuppressed, is_held)).label("held"),
            func.count()
            .filter(and_(unsuppressed, not_held, is_excluded))
            .label("excluded"),
            func.count()
            .filter(and_(unsuppressed, not_held, not_excluded))
            .label("usable"),
        ),
        database,
    )
    return (
        query.outerjoin(held, held.c.lead_id == Lead.id)
        .outerjoin(excluded, excluded.c.phone == Lead.phone)
        .group_by(state)
    )


def inventory_snapshot_query(database: str):
    """The aggregate statement, exposed so its query plan can be checked."""
    if database not in DATABASES:
        raise ValueError(f"Unknown Lead Database {database!r}")
    return _aggregate(database)


def compute_inventory_snapshot(session: Session, database: str) -> dict:
    totals = {
        "allRows": 0,
        "suppressed": 0,
        "held": 0,
        "excluded": 0,
        "usable": 0,
    }
    usable_by_state: dict[str, int] = {}
    for row in session.execute(inventory_snapshot_query(database)):
        totals["allRows"] += int(row.all_rows)
        totals["suppressed"] += int(row.suppressed)
        totals["held"] += int(row.held)
        totals["excluded"] += int(row.excluded)
        totals["usable"] += int(row.usable)
        if row.usable:
            usable_by_state[str(row.state)] = (
                usable_by_state.get(str(row.state), 0) + int(row.usable)
            )
    return {
        **totals,
        "usableByState": dict(sorted(usable_by_state.items())),
    }


def store_inventory_snapshot(
    session: Session,
    database: str,
    counts: dict,
    *,
    computed_at: datetime | None = None,
) -> InventorySnapshot:
    row = session.merge(
        InventorySnapshot(
            database=database,
            all_rows=counts["allRows"],
            suppressed=counts["suppressed"],
            held=counts["held"],
            excluded=counts["excluded"],
            usable=counts["usable"],
            usable_by_state=counts["usableByState"],
            computed_at=computed_at or utcnow(),
        )
    )
    session.flush()
    return row


def snapshot_view(row: InventorySnapshot | None, database: str) -> dict | None:
    if row is None:
        return None
    return {
        "database": database,
        "label": DATABASE_LABELS[database],
        "allRows": int(row.all_rows),
        "suppressed": int(row.suppressed),
        "held": int(row.held),
        "excluded": int(row.excluded),
        "usable": int(row.usable),
        "usableByState": dict(row.usable_by_state or {}),
        "computedAt": _as_of_iso(row.computed_at),
    }


def _refresh_job_state(session: Session) -> dict:
    job = session.scalar(
        select(Job)
        .where(
            Job.kind == REFRESH_INVENTORY_JOB,
            Job.status.in_(
                {JobStatus.queued.value, JobStatus.running.value}
            ),
        )
        .order_by(Job.id.desc())
        .limit(1)
    )
    if job is None:
        return {"status": "idle", "jobId": None}
    return {"status": job.status, "jobId": job.id}


def read_inventory_snapshots(session: Session) -> dict:
    """Both databases' latest snapshots. Never counts inventory."""
    return {
        **{
            database: snapshot_view(
                session.get(InventorySnapshot, database),
                database,
            )
            for database in DATABASES
        },
        "refresh": _refresh_job_state(session),
    }


def enqueue_inventory_refresh(session: Session, *, reason: str) -> Job:
    """Queue one refresh, reusing a refresh that has not started yet.

    The waiting row is locked with SKIP LOCKED: a refresh a worker is claiming
    at this moment is skipped, so a new one is queued rather than trusting a
    run that may have started before this transaction's changes commit.
    """
    waiting = session.scalar(
        select(Job)
        .where(
            Job.kind == REFRESH_INVENTORY_JOB,
            Job.status == JobStatus.queued.value,
        )
        .order_by(Job.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if waiting is not None:
        return waiting
    return enqueue_job(
        session,
        REFRESH_INVENTORY_JOB,
        payload={"reason": reason},
    )


def refresh_inventory_snapshots(session: Session) -> dict:
    """Recount both databases and the Fulfillment pool breakdown."""
    from .nightly_snapshots import settle_nightly_review_inventory
    from .pool_analytics import refresh_pool_breakdown

    if session.get_bind().dialect.name == "postgresql":
        # Scoped to this transaction. Production PostgreSQL runs in Docker
        # with the default 64 MB /dev/shm, and parallel hash joins build their
        # shared hash tables there: with a raised work_mem the DB2 join fails
        # with "could not resize shared memory segment". A serial plan keeps
        # each hash in private memory; work_mem keeps the hold, exclusion, and
        # membership key sets from spilling to disk in batches.
        session.execute(text("SET LOCAL max_parallel_workers_per_gather = 0"))
        session.execute(text("SET LOCAL work_mem = '128MB'"))
    stored = {}
    for database in DATABASES:
        started = utcnow()
        counts = compute_inventory_snapshot(session, database)
        stored[database] = store_inventory_snapshot(
            session,
            database,
            counts,
            computed_at=utcnow(),
        )
        log.info(
            "Inventory snapshot %s: %s usable of %s rows in %.1fs",
            database,
            counts["usable"],
            counts["allRows"],
            (utcnow() - started).total_seconds(),
        )
    # Inventory's pool analytics reads the state x Niche breakdown, so keep
    # it on the same schedule.
    refresh_pool_breakdown(session)
    settle_nightly_review_inventory(session, stored["db1"])
    return read_inventory_snapshots(session)
