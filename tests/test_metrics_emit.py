from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from jawnix.config import Settings
from jawnix.database import Base
from jawnix.metrics_emit import (
    EMIT_LEAD_ASSIGNED_JOB,
    emit_lead_assigned,
    lead_assigned_body,
)
from jawnix.models import (
    Agent,
    DistributionEvent,
    Job,
    JobStatus,
    Lead,
    LeadRequest,
)
from jawnix.worker import process_job

from conftest import make_request


class Response:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def test_lead_assigned_body_uses_distribution_event_id_and_delivered_at():
    delivered = datetime(2026, 8, 3, 18, 0, 0, tzinfo=timezone.utc)
    event = DistributionEvent(
        id=42,
        lead_id=1,
        agent_id=7,
        phone="2145550001",
        state="TX",
        listing_provenance={"kind": "legacy", "source": "facebook"},
        source_kind="legacy",
        delivered_at=delivered,
        source="request",
    )
    body = lead_assigned_body(event)
    assert body == {
        "dedup_key": "42",
        "type": "lead.assigned",
        "actor": "system",
        "occurred_at": "2026-08-03T18:00:00Z",
        "source_agent_identity": "7",
        "contact_ref": {
            "source_link": "jawnix:42",
            "phone": "2145550001",
        },
        "payload": {
            "agent": "7",
            "phone": "2145550001",
            "state": "TX",
            "source": "facebook",
        },
    }


def test_emit_lead_assigned_posts_each_event(session, settings, monkeypatch):
    settings.metrics_ingest_url = "https://metrics.example/ingest/jawnix"
    settings.metrics_ingest_secret = "shared-secret"
    agent = Agent(slug="emit-agent", name="Emit Agent")
    session.add(agent)
    session.flush()
    session.add_all(
        [
            Lead(phone="2145550101", title="One", state="TX"),
            Lead(phone="2145550102", title="Two", state="TX"),
        ]
    )
    request = make_request(session, agent, 2)
    delivered = datetime(2026, 8, 3, 12, 0, 0, tzinfo=timezone.utc)
    leads = list(session.scalars(select(Lead)))
    for lead in leads:
        session.add(
            DistributionEvent(
                lead_id=lead.id,
                agent_id=agent.id,
                request_id=request.id,
                phone=lead.phone,
                title=lead.title,
                state=lead.state,
                listing_provenance={"kind": "legacy", "source": lead.source_flow or "legacy"},
                source_kind="legacy",
                delivered_at=delivered,
                source="request",
            )
        )
    session.flush()

    posts: list[dict] = []

    def capture(*_args, **kwargs):
        posts.append(kwargs)
        return Response(201, {"status": "accepted"})

    monkeypatch.setattr("jawnix.metrics_emit.httpx.post", capture)
    posted = emit_lead_assigned(session, request.id, settings)

    assert posted == 2
    assert len(posts) == 2
    assert all(
        post["headers"]["X-Ingest-Secret"] == "shared-secret" for post in posts
    )
    assert {post["json"]["dedup_key"] for post in posts} == {
        str(event.id)
        for event in session.scalars(
            select(DistributionEvent).where(
                DistributionEvent.request_id == request.id
            )
        )
    }
    for post in posts:
        body = post["json"]
        assert body["type"] == "lead.assigned"
        assert body["actor"] == "system"
        assert body["occurred_at"] == "2026-08-03T12:00:00Z"
        assert body["payload"]["agent"] == str(agent.id)
        assert body["source_agent_identity"] == str(agent.id)


def test_emit_lead_assigned_skips_when_unconfigured(session, settings, monkeypatch):
    agent = Agent(slug="skip-agent", name="Skip Agent")
    session.add(agent)
    session.flush()
    session.add(Lead(phone="2145550201", title="One", state="TX"))
    request = make_request(session, agent, 1)
    lead = session.scalar(select(Lead))
    session.add(
        DistributionEvent(
            lead_id=lead.id,
            agent_id=agent.id,
            request_id=request.id,
            phone=lead.phone,
            state=lead.state,
            delivered_at=datetime.now(timezone.utc),
            source="request",
        )
    )
    session.flush()

    calls = 0

    def boom(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("should not POST when unconfigured")

    monkeypatch.setattr("jawnix.metrics_emit.httpx.post", boom)
    assert emit_lead_assigned(session, request.id, settings) == 0
    assert calls == 0


def test_emit_lead_assigned_http_failure_raises(session, settings, monkeypatch):
    settings.metrics_ingest_url = "https://metrics.example/ingest/jawnix"
    settings.metrics_ingest_secret = "shared-secret"
    agent = Agent(slug="fail-agent", name="Fail Agent")
    session.add(agent)
    session.flush()
    session.add(Lead(phone="2145550301", title="One", state="TX"))
    request = make_request(session, agent, 1)
    lead = session.scalar(select(Lead))
    session.add(
        DistributionEvent(
            lead_id=lead.id,
            agent_id=agent.id,
            request_id=request.id,
            phone=lead.phone,
            state=lead.state,
            delivered_at=datetime.now(timezone.utc),
            source="request",
        )
    )
    session.flush()
    monkeypatch.setattr(
        "jawnix.metrics_emit.httpx.post",
        lambda *_args, **_kwargs: Response(503, text="unavailable"),
    )
    with pytest.raises(RuntimeError, match="503"):
        emit_lead_assigned(session, request.id, settings)


def test_worker_processes_emit_lead_assigned_job(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'emit-worker.db'}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    with factory.begin() as session:
        agent = Agent(slug="worker-emit", name="Worker Emit")
        lead = Lead(phone="2145550401", title="One", state="TX")
        session.add_all([agent, lead])
        session.flush()
        request = LeadRequest(
            user_id=uuid.uuid4(),
            agent=agent,
            lead_count=1,
            state_mode="selected",
            states_snapshot=["TX"],
            delivery_email="emit@example.com",
            status="generated",
        )
        session.add(request)
        session.flush()
        event = DistributionEvent(
            lead_id=lead.id,
            agent_id=agent.id,
            request_id=request.id,
            phone=lead.phone,
            title=lead.title,
            state=lead.state,
            listing_provenance={"kind": "legacy", "source": "google_maps"},
            source_kind="legacy",
            delivered_at=datetime(2026, 8, 3, 15, 30, 0, tzinfo=timezone.utc),
            source="request",
        )
        session.add(event)
        session.flush()
        job = Job(
            kind=EMIT_LEAD_ASSIGNED_JOB,
            request_id=request.id,
            status=JobStatus.running.value,
        )
        session.add(job)
        session.flush()
        job_id = job.id
        event_id = event.id
        agent_id = agent.id

    settings = Settings(
        JAWNIX_METRICS_INGEST_URL="https://metrics.example/ingest/jawnix",
        JAWNIX_METRICS_INGEST_SECRET="worker-secret",
    )
    posts: list[dict] = []

    def capture(*_args, **kwargs):
        posts.append(kwargs)
        return Response(200, {"status": "duplicate"})

    monkeypatch.setattr("jawnix.worker.SessionLocal", factory)
    monkeypatch.setattr("jawnix.worker.get_settings", lambda: settings)
    monkeypatch.setattr("jawnix.metrics_emit.httpx.post", capture)

    process_job(job_id)

    with factory() as session:
        job = session.get(Job, job_id)
        assert job.status == JobStatus.complete.value
        assert job.last_error == ""
    assert len(posts) == 1
    body = posts[0]["json"]
    assert body["dedup_key"] == str(event_id)
    assert body["type"] == "lead.assigned"
    assert body["payload"] == {
        "agent": str(agent_id),
        "phone": "2145550401",
        "state": "TX",
        "source": "google_maps",
    }
    assert body["occurred_at"] == "2026-08-03T15:30:00Z"
    engine.dispose()


def test_worker_emit_failure_does_not_fail_the_request(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'emit-fail.db'}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    with factory.begin() as session:
        agent = Agent(slug="emit-fail", name="Emit Fail")
        lead = Lead(phone="2145550501", title="One", state="TX")
        session.add_all([agent, lead])
        session.flush()
        request = LeadRequest(
            user_id=uuid.uuid4(),
            agent=agent,
            lead_count=1,
            state_mode="selected",
            states_snapshot=["TX"],
            delivery_email="emit-fail@example.com",
            status="generated",
        )
        session.add(request)
        session.flush()
        session.add(
            DistributionEvent(
                lead_id=lead.id,
                agent_id=agent.id,
                request_id=request.id,
                phone=lead.phone,
                state=lead.state,
                delivered_at=datetime.now(timezone.utc),
                source="request",
            )
        )
        job = Job(
            kind=EMIT_LEAD_ASSIGNED_JOB,
            request_id=request.id,
            status=JobStatus.running.value,
        )
        session.add(job)
        session.flush()
        job_id = job.id
        request_id = request.id

    settings = Settings(
        JAWNIX_METRICS_INGEST_URL="https://metrics.example/ingest/jawnix",
        JAWNIX_METRICS_INGEST_SECRET="worker-secret",
    )
    monkeypatch.setattr("jawnix.worker.SessionLocal", factory)
    monkeypatch.setattr("jawnix.worker.get_settings", lambda: settings)
    monkeypatch.setattr(
        "jawnix.metrics_emit.httpx.post",
        lambda *_args, **_kwargs: Response(503),
    )

    process_job(job_id)

    with factory() as session:
        job = session.get(Job, job_id)
        request = session.get(LeadRequest, request_id)
        assert job.status == JobStatus.failed.value
        assert "503" in job.last_error
        assert request.status == "generated"
    engine.dispose()
