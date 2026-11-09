"""The Today page: one oldest-first Decisions list, last night, and batches.

Seam: seeded operational records in; ``today()`` and ``GET /api/admin/today``
out. The Decisions list must agree with the "Decisions waiting" number.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi.testclient import TestClient

from jawnix import today as today_module
from jawnix.api import app
from jawnix.auth import Principal, require_admin
from jawnix.database import get_db
from jawnix.models import (
    Agent,
    BatchArtifact,
    CustomerProfile,
    CustomerRegistration,
    DatasetPublication,
    DistributionEvent,
    EligibilityHold,
    ExclusionList,
    InventoryConflict,
    InventorySyncAttempt,
    Job,
    JobStatus,
    Lead,
    LeadReport,
    LeadRequest,
    NightlyReview,
    ScrapeAnomaly,
    ScrapeSegmentResult,
    ScraperConfiguration,
    ScraperRun,
    SourceNicheMapping,
)
from jawnix.today import today

NOW = datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc)


def customer(session, slug: str) -> Agent:
    item = Agent(slug=slug, name=slug.replace("-", " ").title())
    session.add(item)
    session.flush()
    return item


def batch(session, owner: Agent, status: str, *, created_at: datetime, **kwargs) -> LeadRequest:
    user_id = uuid.uuid4()
    session.add(
        CustomerProfile(user_id=user_id, email=f"{user_id}@example.com", agent=owner)
    )
    session.flush()
    item = LeadRequest(
        user_id=user_id,
        agent=owner,
        lead_count=kwargs.pop("lead_count", 25_000),
        states_snapshot=["FL"],
        delivery_email="ops@example.com",
        status=status,
        created_at=created_at,
        **kwargs,
    )
    session.add(item)
    session.flush()
    return item


def artifact(session, item: LeadRequest, **kwargs) -> None:
    session.add(
        BatchArtifact(
            request_id=item.id,
            path="/nonexistent.csv",
            filename="batch.csv",
            row_count=item.lead_count,
            parts=[],
            byte_count=1,
            sha256="a" * 64,
            expires_at=NOW + timedelta(days=30),
            **kwargs,
        )
    )
    session.flush()


def seed(session) -> dict:
    northstar = customer(session, "northstar-insurance")
    pinecrest = customer(session, "pinecrest-agency")
    gulfshore = customer(session, "gulfshore-advisors")
    pending = batch(session, northstar, "pending", created_at=NOW - timedelta(hours=2))
    delivery_failed = batch(
        session, pinecrest, "failed", created_at=NOW - timedelta(hours=6),
        closed_at=NOW - timedelta(hours=3),
    )
    artifact(
        session, delivery_failed, delivery_status="failed", delivery_attempts=2,
        last_error="Email provider returned 503", created_at=NOW - timedelta(hours=3, minutes=5),
    )
    generation_failed = batch(
        session, gulfshore, "failed", created_at=NOW - timedelta(hours=8),
        status_message="Allocation failed before a batch was generated.",
    )
    waiting = batch(
        session, gulfshore, "waiting_inventory", created_at=NOW - timedelta(days=3),
        available_count=10_000,
    )
    generating = batch(session, northstar, "processing", created_at=NOW - timedelta(minutes=20))
    delivered_today = batch(
        session, northstar, "delivered", created_at=NOW - timedelta(hours=9),
        delivered_at=NOW - timedelta(hours=4),
    )
    artifact(session, delivered_today)
    delivered_yesterday = batch(
        session, northstar, "delivered", created_at=NOW - timedelta(days=2),
        delivered_at=NOW - timedelta(days=1),
    )
    artifact(session, delivered_yesterday)

    lead = Lead(phone="2155550199", title="Dental", state="FL")
    session.add(lead)
    session.flush()
    event = DistributionEvent(
        lead_id=lead.id, agent_id=gulfshore.id, request_id=delivered_yesterday.id
    )
    session.add(event)
    session.flush()
    report = LeadReport(
        distribution_event_id=event.id,
        customer_id=gulfshore.id,
        reason="invalid_phone",
        created_at=NOW - timedelta(days=1),
    )
    session.add(report)
    session.flush()
    session.add(
        EligibilityHold(
            lead_id=lead.id,
            distribution_event_id=event.id,
            report_id=report.id,
            reason="invalid_phone",
            created_at=NOW - timedelta(days=1),
        )
    )

    configuration = ScraperConfiguration(
        version=1, checksum="c" * 64, status="active", anomaly_thresholds={},
        created_by=uuid.uuid4(), reason="Fixture",
    )
    session.add(configuration)
    session.flush()
    committed_run = ScraperRun(
        source="google_maps", source_version="41", configuration_id=configuration.id,
        status="complete",
    )
    held_run = ScraperRun(
        source="google_maps", source_version="42", configuration_id=configuration.id,
        status="held_anomaly",
    )
    session.add_all([committed_run, held_run])
    session.flush()
    publication = DatasetPublication(
        version=41, checksum="d" * 64, scraper_run_id=committed_run.id,
        configuration_id=configuration.id, sync_status="complete",
        committed_at=NOW - timedelta(hours=10),
    )
    session.add(publication)
    session.flush()
    session.add_all(
        [
            InventorySyncAttempt(
                dataset_publication_id=publication.id, dataset_version=41,
                dataset_checksum="d" * 64, attempt_number=1, status="complete",
                result={"imported": 18_420},
            ),
            NightlyReview(
                scraper_run_id=committed_run.id, review_date=date(2026, 11, 9),
                status="complete", telegram_delivery_state="sent",
                telegram_delivery_started_at=NOW - timedelta(hours=9, minutes=20),
                created_at=NOW - timedelta(hours=9, minutes=30),
            ),
            ScrapeSegmentResult(
                scraper_run_id=held_run.id, segment_key="TX::roofing", niche="Roofing",
                geography="Austin, TX", observed_count=40, valid_count=40, new_count=0,
                duplicate_count=0, quarantined_count=0, anomalous=True,
                anomaly_reasons=["more_than_50_percent_down"],
            ),
            ScrapeAnomaly(
                scraper_run_id=held_run.id, configuration_id=configuration.id,
                dataset_checksum="e" * 64, status="pending",
                created_at=NOW - timedelta(hours=4),
            ),
            InventoryConflict(
                older_request_id=waiting.id, newer_request_id=pending.id,
                inventory_snapshot={"sharedLeadIds": [1, 2, 3]},
                snapshot_checksum="f" * 64, status="pending",
                created_at=NOW - timedelta(hours=5),
            ),
            # Behind the failed batch: that batch is the decision.
            Job(kind="deliver_request", request_id=delivery_failed.id,
                status=JobStatus.failed.value, created_at=NOW - timedelta(hours=3)),
            Job(kind="allocate_request", request_id=waiting.id, attempts=2,
                status=JobStatus.failed.value, last_error="Timed out",
                created_at=NOW - timedelta(hours=7)),
            ExclusionList(
                customer_id=northstar.id, uploaded_by="customer", exclusion_type="dnc",
                filename="dnc.csv", storage_path="/tmp/dnc.csv",
                status="pending_confirmation", pool_impact=61, accepted_rows=248,
                created_at=NOW - timedelta(days=2),
            ),
            SourceNicheMapping(
                segment_key="TX::roofer", state="TX", keyword="roofer", niche="Roofing",
                confirmed=False, updated_at=NOW - timedelta(hours=11),
            ),
            CustomerRegistration(
                auth_user_id=uuid.uuid4(), email="new@example.com", first_name="New",
                last_name="Customer", phone="2155550100", licensed_states=["TX"],
                status="awaiting_review", created_at=NOW - timedelta(hours=12),
            ),
        ]
    )
    session.commit()
    return {
        "pending": pending,
        "delivery_failed": delivery_failed,
        "generation_failed": generation_failed,
        "waiting": waiting,
        "generating": generating,
        "delivered_today": delivered_today,
        "report": report,
        "held_run": held_run,
        "committed_run": committed_run,
    }


def test_decisions_merge_every_kind_oldest_first_and_match_the_number(session):
    records = seed(session)
    result = today(session, delivered_since=NOW - timedelta(hours=12), now=NOW)
    items = result["decisions"]["items"]
    assert [item["label"] for item in items] == [
        "Exclusion list",
        "Lead report",
        "Registration",
        "Niche mapping",
        "Failed batch",
        "Failed job",
        "Inventory conflict",
        "Scrape anomaly",
        "Delivery failed",
        "Approval",
    ]
    assert result["decisions"]["total"] == result["numbers"].decisions.waiting == 10
    assert result["decisions"]["unavailable"] == []

    by_kind = {item["kind"]: item for item in items}
    approval = by_kind["approval"]
    assert approval["subject"] == "Northstar Insurance"
    assert approval["detail"] == "25,000 leads · FL"
    assert approval["batch"] == {
        "id": str(records["pending"].id),
        "number": records["pending"].number,
    }
    assert approval["href"] == f"/app/admin/batches/{records['pending'].number}"
    assert by_kind["deliveryFailed"]["detail"] == "Email provider returned 503 · 2 attempts"
    assert by_kind["failedBatch"]["detail"] == "Allocation failed before a batch was generated."
    # The report and its Eligibility Hold are one decision.
    report = by_kind["leadReport"]
    assert report["detail"] == "Invalid phone · hold active"
    assert report["href"] == f"/app/admin/fulfillment/reports/{records['report'].id}"
    assert [item["kind"] for item in items].count("leadReport") == 1
    assert by_kind["inventoryConflict"]["subject"] == "Gulfshore Advisors ↔ Northstar Insurance"
    assert by_kind["inventoryConflict"]["detail"] == "3 overlapping leads"
    job = by_kind["failedJob"]
    assert job["subject"] == f"Allocate #{records['waiting'].number}"
    assert job["detail"] == "Failed after 2 attempts · Timed out"
    assert job["facts"]["affected"]["href"] == f"/app/admin/batches/{records['waiting'].number}"
    anomaly = by_kind["scrapeAnomaly"]
    assert anomaly["subject"] == f"Run {records['held_run'].id}"
    assert anomaly["detail"] == "roofing · TX · down more than 50%"
    assert anomaly["facts"]["segments"][0]["label"] == "roofing · TX"
    assert by_kind["exclusionList"]["detail"] == "DNC · 61 in pool"
    # Exclusion Lists moved to Inventory (A4); the full page opens this list.
    assert by_kind["exclusionList"]["href"] == (
        f"/app/admin/inventory/exclusions?list={by_kind['exclusionList']['recordId']}"
    )
    assert by_kind["nicheMapping"]["subject"] == "roofer · TX"
    assert by_kind["nicheMapping"]["facts"]["segment"] == "TX::roofer"
    assert by_kind["registration"]["subject"] == "New Customer"
    assert by_kind["registration"]["facts"]["matches"] == []


def test_last_night_reads_the_committed_run_and_its_review(session):
    records = seed(session)
    result = today(session, now=NOW)["lastNight"]
    assert result["run"]["id"] == records["committed_run"].id
    assert result["run"]["committedAt"] == NOW - timedelta(hours=10)
    assert result["newLeads"] == 18_420
    assert result["heldSegments"] == 1
    assert result["review"]["telegram"] == "sent"
    assert result["review"]["telegramAt"] == NOW - timedelta(hours=9, minutes=20)


def test_last_night_is_empty_before_any_run(session):
    assert today(session, now=NOW)["lastNight"] is None


def test_batches_in_progress_and_delivered_since_local_midnight(session):
    records = seed(session)
    batches = today(session, delivered_since=NOW - timedelta(hours=12), now=NOW)["batches"]
    assert [row["number"] for row in batches["inProgress"]] == [
        records["waiting"].number,
        records["generating"].number,
    ]
    assert [row["stage"] for row in batches["inProgress"]] == ["waiting", "generating"]
    # A failed delivery whose file was made today belongs to today's work.
    # Newest first.
    assert [row["number"] for row in batches["deliveredToday"]] == [
        records["delivery_failed"].number,
        records["delivered_today"].number,
    ]
    assert batches["deliveredToday"][0]["stage"] == "delivery_failed"
    assert batches["allBatches"] == 7


def test_one_failing_decision_source_leaves_the_rest(session, monkeypatch):
    seed(session)

    def broken(_db):
        raise RuntimeError("source down")

    readers = tuple(
        (key, broken if key == "scrapeAnomalies" else reader)
        for key, reader in today_module._DECISION_READERS
    )
    monkeypatch.setattr(today_module, "_DECISION_READERS", readers)
    result = today(session, now=NOW)
    assert result["decisions"]["unavailable"] == ["scrapeAnomalies"]
    assert "Scrape anomaly" not in [item["label"] for item in result["decisions"]["items"]]
    assert len(result["decisions"]["items"]) == 9


def test_today_endpoint_serves_camel_case(session):
    records = seed(session)

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=uuid.uuid4(), email="admin@example.com", role="admin", csrf="test"
    )
    try:
        body = TestClient(app).get(
            "/api/admin/today", params={"deliveredSince": "2026-11-09T05:00:00Z"}
        ).json()
    finally:
        app.dependency_overrides.clear()
    assert body["numbers"]["decisions"]["waiting"] == body["decisions"]["total"]
    assert body["lastNight"]["newLeads"] == 18_420
    assert body["decisions"]["items"][-1]["batch"]["number"] == records["pending"].number
    assert body["batches"]["allBatches"] == 7
