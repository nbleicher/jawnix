"""Run with JAWNIX_ACCEPTANCE_DATABASE_URL against a disposable PostgreSQL DB."""
import asyncio
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from jawnix.config import Settings
from jawnix.db_notifications import JOB_CHANNEL
from jawnix.job_wakeup import JobWakeup, postgres_dsn
from jawnix.database import Base
from jawnix.jobs import enqueue_job
from jawnix.models import Customer, CustomerProfile, Job, LeadRequest
from jawnix.request_updates import RequestUpdates

DATABASE_URL = os.environ.get("JAWNIX_ACCEPTANCE_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="requires disposable PostgreSQL")


@pytest.fixture
def pg_engine():
    engine = create_engine(DATABASE_URL)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


def test_job_wakeup_occurs_after_commit_and_not_after_rollback(pg_engine):
    engine = pg_engine
    settings = Settings(_env_file=None, database_url=DATABASE_URL, JAWNIX_WORKER_LANE="fulfillment", JAWNIX_WORKER_POLL_SECONDS=2)
    wakeup = JobWakeup(settings)
    wakeup.connect()
    try:
        with Session(engine) as db, ThreadPoolExecutor(max_workers=1) as executor:
            waiting = executor.submit(wakeup.wait)
            enqueue_job(db, "fulfill_round_robin")
            time.sleep(0.05)
            assert not waiting.done(), "Uncommitted job woke the worker"
            started = time.perf_counter()
            db.commit()
            waiting.result(timeout=0.5)
            assert time.perf_counter()-started < 0.5
        with psycopg.connect(postgres_dsn(DATABASE_URL), autocommit=True) as listener, Session(engine) as db:
            listener.execute(f"LISTEN {JOB_CHANNEL}")
            enqueue_job(db, "fulfill_round_robin")
            db.rollback()
            assert list(listener.notifies(timeout=0.05)) == []
    finally:
        wakeup.close()
        engine.dispose()


def test_request_changes_reach_the_api_listener_only_after_commit(pg_engine):
    async def check():
        hub = RequestUpdates(DATABASE_URL)
        await hub.start()
        engine = pg_engine
        try:
            for _ in range(100):
                if hub.connected: break
                await asyncio.sleep(0.01)
            assert hub.connected
            request_id = uuid.uuid4()
            async with hub.subscribe({str(request_id)}) as subscriber, hub.subscribe({"unrelated"}) as unrelated:
                with Session(engine) as db:
                    customer = Customer(slug=f"push-{request_id}", name="Push fixture")
                    profile = CustomerProfile(user_id=uuid.uuid4(), email=f"push-{request_id}@example.test", agent=customer)
                    db.add_all([customer, profile])
                    db.flush()
                    db.add(LeadRequest(id=request_id, user_id=profile.user_id, agent_id=profile.agent_id, lead_count=1, states_snapshot=["TX"], delivery_email=profile.email, status="pending"))
                    db.flush()
                    await asyncio.sleep(0.02)
                    assert not subscriber.changed.is_set()
                    db.commit()
                    await asyncio.wait_for(subscriber.changed.wait(), timeout=1)
                    assert subscriber.pending == {str(request_id)}
                    assert not unrelated.changed.is_set()
        finally:
            await hub.stop()
            engine.dispose()
    asyncio.run(check())


def test_fulfillment_execution_waits_for_the_other_rotation(pg_engine, monkeypatch):
    from jawnix.worker import process_job
    factory = sessionmaker(bind=pg_engine, expire_on_commit=False)
    monkeypatch.setattr("jawnix.worker.SessionLocal", factory)
    with factory.begin() as db:
        job = Job(kind="fulfill_round_robin", status="running", payload={})
        db.add(job)
        db.flush()
        job_id = job.id
    with factory() as locker, ThreadPoolExecutor(max_workers=1) as executor:
        locker.execute(text("SELECT pg_advisory_xact_lock(641901)"))
        running = executor.submit(process_job, job_id)
        time.sleep(0.05)
        assert not running.done(), "Another rotation ran concurrently"
        locker.rollback()
        running.result(timeout=2)
    with factory() as db:
        assert db.get(Job, job_id).status == "complete"
