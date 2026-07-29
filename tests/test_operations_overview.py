"""The resilient administrator Operations overview contract (#75)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from jawnix import operations_overview as overview
from jawnix.api import app
from jawnix.auth import Principal, require_admin
from jawnix.database import get_db
from jawnix.models import Job, JobStatus, NightlyReview


def as_admin(session) -> TestClient:
    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=uuid.uuid4(),
        email="admin@example.com",
        role="admin",
        csrf="test",
    )
    return TestClient(app)


def test_empty_sources_are_available_and_do_not_invent_work(session):
    result = overview.operations_overview(session)

    assert result.degraded is False
    assert result.available_count == 0
    assert [source.key for source in result.sources] == [
        "fulfillment",
        "backgroundJobs",
        "acquisition",
    ]
    assert all(source.status == "available" for source in result.sources)
    assert all(source.count == 0 for source in result.sources)


def test_failed_jobs_are_counted_and_route_to_their_owning_workspace(session):
    job = Job(
        kind="run_scraper",
        status=JobStatus.failed.value,
        attempts=2,
        last_error=(
            "postgresql://private-user:private-password@10.0.0.4/internal"
        ),
    )
    session.add(job)
    session.flush()

    source = overview.read_jobs_source(session)

    assert source.count == 1
    item = source.queues[0].items[0]
    assert item.title == f"Run scraper · Job {job.id}"
    assert item.action.href == "/app/admin/acquisition"
    assert item.action.label == "Open owning workspace"
    # Raw worker exceptions can contain private topology or credentials. The
    # Overview identifies the failed job without turning it into a log viewer.
    assert "private-password" not in item.summary
    assert "10.0.0.4" not in item.summary


def test_nightly_review_count_is_not_capped_by_workspace_history(session):
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    session.add(
        NightlyReview(
            status="waiting_publication",
            summary={"failures": [{"kind": "publication_delayed"}]},
            telegram_delivery_state="pending",
            created_at=start,
        )
    )
    for day in range(1, 12):
        session.add(
            NightlyReview(
                status="complete",
                summary={"failures": []},
                telegram_delivery_state="sent",
                created_at=start + timedelta(days=day),
            )
        )
    session.flush()

    source = overview.read_acquisition_source(session)
    reviews = next(
        queue
        for queue in source.queues
        if queue.key == "nightlyReviews"
    )

    assert reviews.count == 1
    assert len(reviews.items) == 1
    assert reviews.items[0].status == "Waiting publication"


def test_breaking_one_source_degrades_only_that_section(
    session,
    monkeypatch,
):
    def unavailable(_db):
        raise RuntimeError(
            "Fulfillment database password and private topology"
        )

    monkeypatch.setattr(
        overview,
        "read_fulfillment_source",
        unavailable,
    )
    session.add(
        Job(
            kind="run_scraper",
            status=JobStatus.failed.value,
            attempts=1,
            last_error="Worker stopped.",
        )
    )
    session.flush()

    client = as_admin(session)
    try:
        response = client.get("/api/admin/operations-overview")
    finally:
        app.dependency_overrides.clear()
    body = response.json()
    sources = {source["key"]: source for source in body["sources"]}

    assert response.status_code == 200
    assert body["degraded"] is True
    assert sources["fulfillment"]["status"] == "unavailable"
    assert sources["fulfillment"]["count"] is None
    assert sources["fulfillment"]["queues"] == []
    assert "password" not in sources["fulfillment"]["errorDescription"]
    assert sources["backgroundJobs"]["status"] == "available"
    assert sources["backgroundJobs"]["count"] == 1
    assert sources["acquisition"]["status"] == "available"
    assert sources["acquisition"]["count"] == 0
    assert body["availableCount"] == 1


def test_endpoint_exposes_the_stable_camel_case_contract(session):
    session.add(
        Job(
            kind="sync_inventory",
            status=JobStatus.failed.value,
            attempts=3,
            last_error="Inventory Sync stopped.",
        )
    )
    session.commit()
    client = as_admin(session)
    try:
        response = client.get("/api/admin/operations-overview")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "generatedAt",
        "availableCount",
        "degraded",
        "sources",
    }
    assert body["availableCount"] == 1
    jobs = next(
        source
        for source in body["sources"]
        if source["key"] == "backgroundJobs"
    )
    assert jobs["count"] == 1
    assert jobs["queues"][0]["key"] == "failedJobs"
    assert jobs["queues"][0]["items"][0]["nextAction"] == (
        "Review affected record"
    )
    assert jobs["queues"][0]["items"][0]["recordedAt"]
