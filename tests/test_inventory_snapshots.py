"""Usable DB1 and DB2 inventory snapshots (B3, B4).

Seam: seeded inventory in; stored snapshot, admin API, and queued background
jobs out. Counting happens only in the refresh job; reads and the refresh
endpoint never count inventory.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from jawnix.api import app
from jawnix.auth import Principal, require_admin
from jawnix.database import Base, get_db
from jawnix.feedback import apply_disposition_controls
from jawnix.inventory_snapshots import (
    REFRESH_INVENTORY_JOB,
    compute_inventory_snapshot,
    enqueue_inventory_refresh,
    refresh_inventory_snapshots,
)
from jawnix.jobs import WORKER_LANE_MAINTENANCE, job_lane
from jawnix.lead_databases import inventory_scope
from jawnix.models import (
    Agent,
    DatasetPublication,
    DistributionEvent,
    ExclusionList,
    ExclusionPhone,
    InventorySnapshot,
    Job,
    JobStatus,
    Lead,
    LeadCorrectionEvent,
    LeadDatabaseMembership,
    LeadDispositionTransition,
    LeadUpload,
    NightlyReview,
    ScraperConfiguration,
    ScraperRun,
)
from jawnix.nightly_snapshots import nightly_operational_snapshot
from jawnix.pool_analytics import _actively_held, _globally_excluded
from jawnix.worker import process_job


ADMIN_ID = uuid.uuid4()


def as_admin(session) -> TestClient:
    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=ADMIN_ID,
        email="admin@example.com",
        role="admin",
        csrf="test",
    )
    return TestClient(app)


def lead(session, phone: str, state: str, **kwargs) -> Lead:
    item = Lead(phone=phone, title=kwargs.pop("title", "Listing"), state=state, **kwargs)
    session.add(item)
    session.flush()
    return item


def hold(session, target: Lead, *, active: bool = True) -> None:
    customer = Agent(slug=f"reporter-{uuid.uuid4().hex[:8]}", name="Reporter")
    session.add(customer)
    session.flush()
    event = DistributionEvent(
        lead_id=target.id,
        customer_id=customer.id,
        customer_name=customer.name,
        phone=target.phone,
        title=target.title,
        state=target.state,
    )
    session.add(event)
    session.flush()
    transition = LeadDispositionTransition(
        distribution_event_id=event.id,
        customer_id=customer.id,
        actor_user_id=uuid.uuid4(),
        disposition="invalid_phone",
        note="Disconnected",
    )
    session.add(transition)
    session.flush()
    _, created = apply_disposition_controls(session, event, transition)
    created.active = active
    session.flush()


def global_exclusion(session, *phones: str, effective: bool = True) -> None:
    item = ExclusionList(
        customer_id=None,
        uploaded_by="admin",
        exclusion_type="dnc",
        filename="dnc.csv",
        storage_path="/tmp/dnc.csv",
        status="confirmed" if effective else "pending_confirmation",
        global_effective=effective,
    )
    session.add(item)
    session.flush()
    session.add_all(
        ExclusionPhone(exclusion_list_id=item.id, phone=phone) for phone in phones
    )
    session.flush()


def db2_upload(session, members: list[tuple[Lead, str]]) -> None:
    upload = LeadUpload(
        operator_id=uuid.uuid4(),
        filename="db2.csv",
        path="fixture",
        state="TN",
        status="complete",
    )
    session.add(upload)
    session.flush()
    session.add_all(
        LeadDatabaseMembership(lead_id=item.id, upload_id=upload.id, state=state)
        for item, state in members
    )
    session.flush()


def seed_inventory(session) -> dict[str, Lead]:
    leads = {
        "usable_tx": lead(session, "2145550001", "TX"),
        "usable_fl": lead(session, "3055550002", "FL"),
        "suppressed": lead(session, "2145550003", "TX", suppressed=True),
        "held": lead(session, "2145550004", "TX"),
        "released": lead(session, "2145550005", "TX"),
        "excluded": lead(session, "2145550006", "TX"),
        # Customer-scoped and unconfirmed lists never bar the pool.
        "pending_list": lead(session, "2145550007", "TX"),
        # Suppression wins: counted once, as suppressed.
        "suppressed_held": lead(session, "2145550008", "TX", suppressed=True),
        # A doubly held, doubly excluded Lead is still one row.
        "double": lead(session, "2145550009", "TX"),
        "db2_only": lead(session, "6155550010", "", title="", in_db1=False),
    }
    hold(session, leads["held"])
    hold(session, leads["released"], active=False)
    hold(session, leads["suppressed_held"])
    hold(session, leads["double"])
    hold(session, leads["double"])
    global_exclusion(session, leads["excluded"].phone, leads["double"].phone)
    global_exclusion(session, leads["double"].phone)
    global_exclusion(session, leads["pending_list"].phone, effective=False)
    return leads


def test_db1_buckets_are_exclusive_and_match_the_pool_rule(session):
    seed_inventory(session)

    counts = compute_inventory_snapshot(session, "db1")

    assert counts == {
        "allRows": 9,
        "suppressed": 2,
        "held": 2,
        "excluded": 1,
        "usable": 4,
        "usableByState": {"FL": 1, "TX": 3},
    }
    assert (
        counts["usable"]
        + counts["suppressed"]
        + counts["held"]
        + counts["excluded"]
        == counts["allRows"]
    )
    # The same rule pool analytics and allocation use, counted the slow way.
    reference = session.scalar(
        inventory_scope(select(func.count(Lead.id)), "db1").where(
            Lead.suppressed.is_(False),
            ~_actively_held(),
            ~_globally_excluded(),
        )
    )
    assert counts["usable"] == reference


def test_db2_uses_membership_state_and_the_same_usable_rule(session):
    leads = seed_inventory(session)
    corrected = lead(session, "6155550011", "KY")
    correction = LeadCorrectionEvent(
        lead_id=corrected.id,
        action="set",
        title="Corrected title",
        state="KY",
        reason="Verified by phone.",
        actor_id="admin",
        based_on_kind="none",
    )
    session.add(correction)
    session.flush()
    corrected.active_correction_id = correction.id
    db2_upload(
        session,
        [
            (leads["db2_only"], "TN"),
            (leads["usable_tx"], "TN"),
            (leads["suppressed"], "TN"),
            (leads["held"], "TN"),
            (leads["excluded"], "TN"),
            (corrected, "TN"),
        ],
    )

    counts = compute_inventory_snapshot(session, "db2")

    assert counts == {
        "allRows": 6,
        "suppressed": 1,
        "held": 1,
        "excluded": 1,
        "usable": 3,
        # A global Lead Correction outranks the uploaded state.
        "usableByState": {"KY": 1, "TN": 2},
    }


def test_reads_never_count_and_refresh_is_queued_once(session):
    seed_inventory(session)
    session.commit()
    client = as_admin(session)
    try:
        empty = client.get("/api/admin/inventory-snapshots")
        assert empty.status_code == 200
        assert empty.json() == {
            "db1": None,
            "db2": None,
            "refresh": {"status": "idle", "jobId": None},
        }

        queued = client.post("/api/admin/inventory-snapshots/refresh")
        again = client.post("/api/admin/inventory-snapshots/refresh")
        assert queued.status_code == again.status_code == 202
        assert queued.json()["refresh"]["status"] == "queued"
        assert again.json()["refresh"] == queued.json()["refresh"]
        assert session.scalar(
            select(func.count(Job.id)).where(Job.kind == REFRESH_INVENTORY_JOB)
        ) == 1
        # Queuing is not counting: nothing was stored yet.
        assert session.get(InventorySnapshot, "db1") is None

        # The pool breakdown refresh joins the same queue instead of counting.
        pool = client.post("/api/admin/pool-breakdown/refresh")
        assert pool.status_code == 200
        assert pool.json()["asOf"] is None
        assert pool.json()["refreshQueued"] is True
        assert pool.json()["jobId"] == queued.json()["refresh"]["jobId"]

        refresh_inventory_snapshots(session)
        session.commit()

        body = client.get("/api/admin/inventory-snapshots").json()
        assert body["db1"]["usable"] == 4
        assert body["db1"]["label"] == "DB1"
        assert body["db1"]["allRows"] == 9
        assert body["db1"]["usableByState"] == {"FL": 1, "TX": 3}
        assert body["db1"]["computedAt"].endswith("Z")
        assert body["db2"] == {
            "database": "db2",
            "label": "DB2",
            "allRows": 0,
            "suppressed": 0,
            "held": 0,
            "excluded": 0,
            "usable": 0,
            "usableByState": {},
            "computedAt": body["db2"]["computedAt"],
        }
        breakdown = client.get("/api/admin/pool-breakdown").json()
        assert breakdown["asOf"] is not None
        assert sum(cell["count"] for cell in breakdown["cells"]) == 4
    finally:
        app.dependency_overrides.clear()


def test_a_running_refresh_does_not_absorb_later_changes(session):
    first = enqueue_inventory_refresh(session, reason="manual")
    assert enqueue_inventory_refresh(session, reason="db2_upload").id == first.id
    first.status = JobStatus.running.value
    session.flush()

    second = enqueue_inventory_refresh(session, reason="inventory_sync")

    assert second.id != first.id
    assert second.status == JobStatus.queued.value
    assert second.payload == {"reason": "inventory_sync"}
    assert job_lane(REFRESH_INVENTORY_JOB) == WORKER_LANE_MAINTENANCE


def test_worker_runs_the_refresh_on_the_maintenance_lane(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'refresh.db'}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    with factory.begin() as session:
        lead(session, "2145550001", "TX")
        lead(session, "2145550002", "TX", suppressed=True)
        job_id = enqueue_inventory_refresh(session, reason="nightly").id

    monkeypatch.setattr("jawnix.worker.SessionLocal", factory)
    process_job(job_id)

    with factory() as session:
        assert session.get(Job, job_id).status == JobStatus.complete.value
        snapshot = session.get(InventorySnapshot, "db1")
        assert (snapshot.usable, snapshot.suppressed, snapshot.all_rows) == (
            1,
            1,
            2,
        )
    engine.dispose()


def test_nightly_snapshot_records_the_stored_usable_count(session):
    lead(session, "2145550001", "TX")
    lead(session, "2145550002", "TX", suppressed=True)

    before = nightly_operational_snapshot(session)["inventory"]
    assert before == {"total": 2, "byState": {"TX": 2}}

    refresh_inventory_snapshots(session)
    after = nightly_operational_snapshot(session)["inventory"]

    assert after["total"] == 2
    assert after["eligible"] == 1
    assert after["eligibleAsOf"].endswith("Z")


def test_refresh_after_a_sync_settles_that_runs_review(session):
    """The review written inside the sync can only quote the pre-sync count."""
    configuration = ScraperConfiguration(
        version=1,
        checksum="c" * 64,
        status="active",
        anomaly_thresholds={},
        created_by=uuid.uuid4(),
        reason="Test",
    )
    session.add(configuration)
    session.flush()
    run = ScraperRun(
        source="google_maps",
        source_version="inventory-snapshot-test",
        configuration_id=configuration.id,
        status="complete",
    )
    session.add(run)
    session.flush()
    lead(session, "2145550001", "TX")
    refresh_inventory_snapshots(session)
    stale = session.get(InventorySnapshot, "db1")
    stale.computed_at = datetime.now(timezone.utc) - timedelta(hours=2)
    session.flush()
    synchronized_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    session.add(
        DatasetPublication(
            version=7,
            checksum="d" * 64,
            scraper_run_id=run.id,
            configuration_id=configuration.id,
            sync_status="complete",
            synchronized_at=synchronized_at,
            committed_at=synchronized_at,
        )
    )
    # The sync added inventory and wrote the review in its transaction.
    lead(session, "2145550002", "TX")
    review = NightlyReview(
        scraper_run_id=run.id,
        status="complete",
        summary={"inventory": nightly_operational_snapshot(session)["inventory"]},
    )
    session.add(review)
    session.flush()
    assert review.summary["inventory"]["eligible"] == 1

    refresh_inventory_snapshots(session)
    session.refresh(review)
    settled = review.summary["inventory"]
    assert settled["total"] == 2
    assert settled["eligible"] == 2

    # Later refreshes leave the settled, durable record alone.
    lead(session, "2145550003", "TX")
    refresh_inventory_snapshots(session)
    session.refresh(review)
    assert review.summary["inventory"]["eligible"] == 2


def test_scheduler_queues_one_nightly_refresh_per_day(tmp_path, monkeypatch):
    from jawnix.config import Settings
    from jawnix_data.scheduler import enqueue_nightly_inventory_refresh

    engine = create_engine(f"sqlite:///{tmp_path / 'scheduler.db'}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    monkeypatch.setattr("jawnix_data.scheduler.SessionLocal", factory)
    settings = Settings(JAWNIX_NIGHTLY_REVIEW_HOUR_UTC=9)
    early = datetime(2026, 11, 9, 8, 59, tzinfo=timezone.utc)
    due = datetime(2026, 11, 9, 9, 0, tzinfo=timezone.utc)

    def queued() -> list[Job]:
        with factory() as session:
            return list(
                session.scalars(
                    select(Job).where(Job.kind == REFRESH_INVENTORY_JOB)
                )
            )

    assert enqueue_nightly_inventory_refresh(
        settings, now=early, last_enqueued=None
    ) is None
    assert queued() == []

    marker = enqueue_nightly_inventory_refresh(
        settings, now=due, last_enqueued=None
    )
    assert marker == due.date()
    assert [job.payload for job in queued()] == [{"reason": "nightly"}]

    # Later loops the same day do nothing; the next day coalesces with a
    # refresh that is still waiting.
    enqueue_nightly_inventory_refresh(
        settings, now=due + timedelta(hours=1), last_enqueued=marker
    )
    enqueue_nightly_inventory_refresh(
        settings, now=due + timedelta(days=1), last_enqueued=marker
    )
    assert len(queued()) == 1
    engine.dispose()
