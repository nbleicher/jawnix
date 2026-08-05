from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from jawnix.config import Settings
from jawnix.database import Base
from jawnix.metrics_emit import (
    EMIT_LEAD_ASSIGNED_JOB,
    EMIT_MAX_ATTEMPTS,
    MetricsEmitTransientError,
    emit_lead_assigned,
    emit_retry_delay,
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


def _patch_metrics_client(monkeypatch, post_fn):
    """Route emit POSTs through ``post_fn`` (same kwargs as httpx.Client.post)."""

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, *args, **kwargs):
            return post_fn(*args, **kwargs)

    monkeypatch.setattr("jawnix.metrics_emit.httpx.Client", FakeClient)


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

    _patch_metrics_client(monkeypatch, capture)
    posted = emit_lead_assigned(session, request.id, settings)

    assert posted.posted == 2
    assert posted.next_after_id is None
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

    _patch_metrics_client(monkeypatch, boom)
    result = emit_lead_assigned(session, request.id, settings)
    assert result.posted == 0
    assert result.next_after_id is None
    assert calls == 0


def _make_single_event_request(session, agent_slug: str, phone: str):
    agent = Agent(slug=agent_slug, name=agent_slug.replace("-", " ").title())
    session.add(agent)
    session.flush()
    session.add(Lead(phone=phone, title="One", state="TX"))
    request = make_request(session, agent, 1)
    lead = session.scalar(select(Lead).where(Lead.phone == phone))
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
    return request


def test_emit_lead_assigned_422_held_body_counts_as_delivered(
    session, settings, monkeypatch
):
    settings.metrics_ingest_url = "https://metrics.example/ingest/jawnix"
    settings.metrics_ingest_secret = "shared-secret"
    request = _make_single_event_request(session, "held-agent", "2145550601")
    _patch_metrics_client(
        monkeypatch,
        lambda *_args, **_kwargs: Response(
            422,
            {
                "status": "held",
                "reason": "unmapped_agent",
                "held_event_id": str(uuid.uuid4()),
                "source": "jawnix",
                "source_identity": "7",
                "detail": "no identity map entry for '7' under ['jawnix']",
            },
        ),
    )
    assert emit_lead_assigned(session, request.id, settings).posted == 1


def test_emit_lead_assigned_non_held_422_raises_with_detail(
    session, settings, monkeypatch
):
    settings.metrics_ingest_url = "https://metrics.example/ingest/jawnix"
    settings.metrics_ingest_secret = "super-secret-value"
    request = _make_single_event_request(session, "reject-agent", "2145550602")
    _patch_metrics_client(
        monkeypatch,
        lambda *_args, **_kwargs: Response(
            422,
            {"detail": {"reason": "missing_source_agent_identity"}},
            text='{"detail": {"reason": "missing_source_agent_identity"}}',
        ),
    )
    with pytest.raises(RuntimeError, match="missing_source_agent_identity") as excinfo:
        emit_lead_assigned(session, request.id, settings)
    assert not isinstance(excinfo.value, MetricsEmitTransientError)
    assert "422" in str(excinfo.value)
    assert "super-secret-value" not in str(excinfo.value)


def test_emit_lead_assigned_other_4xx_raises_permanent(session, settings, monkeypatch):
    settings.metrics_ingest_url = "https://metrics.example/ingest/jawnix"
    settings.metrics_ingest_secret = "shared-secret"
    request = _make_single_event_request(session, "forbidden-agent", "2145550603")
    _patch_metrics_client(monkeypatch, lambda *_args, **_kwargs: Response(403, text="bad ingest secret"),
    )
    with pytest.raises(RuntimeError, match="403") as excinfo:
        emit_lead_assigned(session, request.id, settings)
    assert not isinstance(excinfo.value, MetricsEmitTransientError)
    assert "bad ingest secret" in str(excinfo.value)


def test_emit_lead_assigned_network_error_is_transient(session, settings, monkeypatch):
    import httpx

    settings.metrics_ingest_url = "https://metrics.example/ingest/jawnix"
    settings.metrics_ingest_secret = "shared-secret"
    request = _make_single_event_request(session, "net-agent", "2145550604")

    def explode(*_args, **_kwargs):
        raise httpx.ConnectError("connection refused")

    _patch_metrics_client(monkeypatch, explode)
    with pytest.raises(MetricsEmitTransientError, match="connection refused"):
        emit_lead_assigned(session, request.id, settings)


def test_emit_lead_assigned_5xx_is_transient(session, settings, monkeypatch):
    settings.metrics_ingest_url = "https://metrics.example/ingest/jawnix"
    settings.metrics_ingest_secret = "shared-secret"
    request = _make_single_event_request(session, "server-err-agent", "2145550605")
    _patch_metrics_client(monkeypatch, lambda *_args, **_kwargs: Response(502, text="bad gateway"),
    )
    with pytest.raises(MetricsEmitTransientError, match="502"):
        emit_lead_assigned(session, request.id, settings)


def test_emit_retry_delay_backoff_schedule():
    assert emit_retry_delay(0).total_seconds() == 60
    assert emit_retry_delay(1).total_seconds() == 60
    assert emit_retry_delay(2).total_seconds() == 300
    assert emit_retry_delay(3).total_seconds() == 900
    assert emit_retry_delay(4).total_seconds() == 1800
    assert emit_retry_delay(5).total_seconds() == 3600
    assert emit_retry_delay(EMIT_MAX_ATTEMPTS).total_seconds() == 3600


def test_emit_lead_assigned_warns_once_per_process_when_unconfigured(
    session, settings, monkeypatch, caplog
):
    monkeypatch.setattr("jawnix.metrics_emit._unconfigured_warned", False)
    request = _make_single_event_request(session, "warn-agent", "2145550606")
    _patch_metrics_client(monkeypatch, lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("should not POST when unconfigured")
        ),
    )
    with caplog.at_level("WARNING", logger="jawnix.metrics_emit"):
        assert emit_lead_assigned(session, request.id, settings).posted == 0
        assert emit_lead_assigned(session, request.id, settings).posted == 0
    warnings = [
        record
        for record in caplog.records
        if record.levelname == "WARNING"
        and "JAWNIX_METRICS_INGEST_URL" in record.getMessage()
    ]
    assert len(warnings) == 1


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
    _patch_metrics_client(monkeypatch, lambda *_args, **_kwargs: Response(503, text="unavailable"),
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
    _patch_metrics_client(monkeypatch, capture)

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


def _seed_worker_emit_job(factory, *, attempts: int = 1):
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
            attempts=attempts,
        )
        session.add(job)
        session.flush()
        return job.id, request.id


def test_worker_transient_emit_failure_requeues_then_succeeds(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'emit-retry.db'}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    job_id, request_id = _seed_worker_emit_job(factory, attempts=1)

    settings = Settings(
        JAWNIX_METRICS_INGEST_URL="https://metrics.example/ingest/jawnix",
        JAWNIX_METRICS_INGEST_SECRET="worker-secret",
    )
    responses = [Response(503, text="unavailable"), Response(201, {"status": "accepted"})]

    monkeypatch.setattr("jawnix.worker.SessionLocal", factory)
    monkeypatch.setattr("jawnix.worker.get_settings", lambda: settings)
    _patch_metrics_client(monkeypatch, lambda *_args, **_kwargs: responses.pop(0),
    )

    before = datetime.now(timezone.utc)
    process_job(job_id)

    with factory() as session:
        job = session.get(Job, job_id)
        request = session.get(LeadRequest, request_id)
        assert job.status == JobStatus.queued.value
        assert "503" in job.last_error
        run_after = job.run_after
        if run_after.tzinfo is None:
            run_after = run_after.replace(tzinfo=timezone.utc)
        assert run_after >= before + timedelta(seconds=60)
        assert job.locked_by == ""
        assert request.status == "generated"

    # The rescheduled job runs again once run_after passes and succeeds.
    process_job(job_id)

    with factory() as session:
        job = session.get(Job, job_id)
        assert job.status == JobStatus.complete.value
        assert job.last_error == ""
    assert responses == []
    engine.dispose()


def test_worker_emit_failure_does_not_fail_the_request(tmp_path, monkeypatch):
    # Exhausted attempts on a transient error: the job fails permanently but
    # the customer request is never marked failed.
    engine = create_engine(f"sqlite:///{tmp_path / 'emit-fail.db'}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    job_id, request_id = _seed_worker_emit_job(factory, attempts=EMIT_MAX_ATTEMPTS)

    settings = Settings(
        JAWNIX_METRICS_INGEST_URL="https://metrics.example/ingest/jawnix",
        JAWNIX_METRICS_INGEST_SECRET="worker-secret",
    )
    monkeypatch.setattr("jawnix.worker.SessionLocal", factory)
    monkeypatch.setattr("jawnix.worker.get_settings", lambda: settings)
    _patch_metrics_client(monkeypatch, lambda *_args, **_kwargs: Response(503, text="unavailable"),
    )

    process_job(job_id)

    with factory() as session:
        job = session.get(Job, job_id)
        request = session.get(LeadRequest, request_id)
        assert job.status == JobStatus.failed.value
        assert "503" in job.last_error
        assert request.status == "generated"
    engine.dispose()


def test_emit_lead_assigned_chunks_and_returns_cursor(
    session, settings, monkeypatch
):
    settings.metrics_ingest_url = "https://metrics.example/ingest/jawnix"
    settings.metrics_ingest_secret = "shared-secret"
    agent = Agent(slug="chunk-agent", name="Chunk Agent")
    session.add(agent)
    session.flush()
    session.add_all(
        [
            Lead(phone="2145550701", title="One", state="TX"),
            Lead(phone="2145550702", title="Two", state="TX"),
            Lead(phone="2145550703", title="Three", state="TX"),
        ]
    )
    request = make_request(session, agent, 3)
    delivered = datetime(2026, 8, 5, 12, 0, 0, tzinfo=timezone.utc)
    leads = list(session.scalars(select(Lead).order_by(Lead.id)))
    for lead in leads:
        session.add(
            DistributionEvent(
                lead_id=lead.id,
                agent_id=agent.id,
                request_id=request.id,
                phone=lead.phone,
                title=lead.title,
                state=lead.state,
                delivered_at=delivered,
                source="request",
            )
        )
    session.flush()
    event_ids = list(
        session.scalars(
            select(DistributionEvent.id)
            .where(DistributionEvent.request_id == request.id)
            .order_by(DistributionEvent.id)
        )
    )
    posts: list[str] = []

    def capture(*_args, **kwargs):
        posts.append(kwargs["json"]["dedup_key"])
        return Response(201, {"status": "accepted"})

    _patch_metrics_client(monkeypatch, capture)

    first = emit_lead_assigned(session, request.id, settings, limit=2)
    assert first.posted == 2
    assert first.next_after_id == event_ids[1]
    assert posts == [str(event_ids[0]), str(event_ids[1])]

    second = emit_lead_assigned(
        session,
        request.id,
        settings,
        after_id=first.next_after_id,
        limit=2,
    )
    assert second.posted == 1
    assert second.next_after_id is None
    assert posts == [str(eid) for eid in event_ids]


def test_worker_requeues_emit_continuation_between_chunks(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'emit-chunk-worker.db'}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    with factory.begin() as session:
        agent = Agent(slug="worker-chunk", name="Worker Chunk")
        session.add(agent)
        session.flush()
        leads = [
            Lead(phone=f"21455508{i:02d}", title=f"L{i}", state="TX")
            for i in range(3)
        ]
        session.add_all(leads)
        session.flush()
        request = LeadRequest(
            user_id=uuid.uuid4(),
            agent=agent,
            lead_count=3,
            state_mode="selected",
            states_snapshot=["TX"],
            delivery_email="chunk@example.com",
            status="generated",
        )
        session.add(request)
        session.flush()
        for lead in leads:
            session.add(
                DistributionEvent(
                    lead_id=lead.id,
                    agent_id=agent.id,
                    request_id=request.id,
                    phone=lead.phone,
                    title=lead.title,
                    state=lead.state,
                    delivered_at=datetime(
                        2026, 8, 5, 15, 0, 0, tzinfo=timezone.utc
                    ),
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
    monkeypatch.setattr("jawnix.metrics_emit.EMIT_CHUNK_SIZE", 2)
    _patch_metrics_client(monkeypatch, lambda *_args, **_kwargs: Response(201, {"status": "accepted"}),
    )

    process_job(job_id)

    with factory() as session:
        first = session.get(Job, job_id)
        assert first.status == JobStatus.complete.value
        followups = list(
            session.scalars(
                select(Job)
                .where(
                    Job.kind == EMIT_LEAD_ASSIGNED_JOB,
                    Job.request_id == request_id,
                    Job.id != job_id,
                )
                .order_by(Job.id)
            )
        )
        assert len(followups) == 1
        assert followups[0].status == JobStatus.queued.value
        assert "after_id" in followups[0].payload
        continuation_id = followups[0].id
        followups[0].status = JobStatus.running.value
        followups[0].attempts = 1
        session.commit()

    process_job(continuation_id)

    with factory() as session:
        continuation = session.get(Job, continuation_id)
        assert continuation.status == JobStatus.complete.value
        leftover = list(
            session.scalars(
                select(Job).where(
                    Job.kind == EMIT_LEAD_ASSIGNED_JOB,
                    Job.request_id == request_id,
                    Job.status == JobStatus.queued.value,
                )
            )
        )
        assert leftover == []
    engine.dispose()
