"""Real reserve concurrency, with a model call blocked outside transactions."""

import asyncio
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock

import psycopg
import pytest
from psycopg import sql
from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from jawnix.database import Base
from jawnix.job_wakeup import postgres_dsn
from jawnix.keyword_generation import KeywordGenerationResult, try_generation_lock
from jawnix.keyword_rollover import (
    refill_keyword_reserve,
    run_automatic_keyword_rollover,
)
from jawnix.models import KeywordCandidatePool, KeywordHistory
from scraper_fake import GenerationFake, ScraperFake

DATABASE_URL = os.environ.get("JAWNIX_ACCEPTANCE_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires disposable PostgreSQL"
)


@pytest.fixture
def reserve():
    base = make_url(DATABASE_URL)
    assert base.database.endswith("_test"), "Use a disposable *_test database"
    name = f"jawnix_keyword_{uuid.uuid4().hex[:16]}_test"
    with psycopg.connect(postgres_dsn(DATABASE_URL), autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    engine = create_engine(base.set(database=name), pool_size=6)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    operations = ScraperFake()
    operations.rollover_enabled = True
    operations.rollover_state_override = "working"
    prepared = (
        GenerationFake()
        .generate_keywords(mode="broad", excluded_keywords=[], count=50)
        .terms
    )
    with factory.begin() as db:
        db.add(
            KeywordCandidatePool(
                id=1,
                candidates=[{"term": term, "model": "fixture"} for term in prepared],
            )
        )
    try:
        yield factory, operations, prepared
    finally:
        engine.dispose()
        with psycopg.connect(postgres_dsn(DATABASE_URL), autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
            )


def activate(factory, operations, settings):
    with factory.begin() as db:
        return asyncio.run(
            run_automatic_keyword_rollover(db, settings, operations=operations)
        )


def test_activation_proceeds_while_generation_is_blocked_then_merge_revalidates(
    reserve, settings
):
    factory, operations, prepared = reserve
    started, release = Event(), Event()
    fresh = (
        GenerationFake()
        .generate_keywords(mode="broad", excluded_keywords=["other"], count=8)
        .terms
    )

    class Blocked(GenerationFake):
        def generate_keywords(self, **kwargs):
            started.set()
            assert release.wait(10), "Fixture generation was never released"
            return KeywordGenerationResult(
                terms=[prepared[0], prepared[-1], *fresh],
                excluded_count=0,
                candidate_metrics={},
            )

    with ThreadPoolExecutor(max_workers=1) as executor:
        running = executor.submit(
            lambda: asyncio.run(
                refill_keyword_reserve(
                    factory, settings, operations=operations, provider=Blocked()
                )
            )
        )
        try:
            assert started.wait(5)
            second = GenerationFake()
            assert asyncio.run(
                refill_keyword_reserve(
                    factory, settings, operations=operations, provider=second
                )
            ) == {"outcome": "locked"}
            assert second.calls == []
            with factory.begin() as db:
                assert not try_generation_lock(
                    db
                ), "Manual generation must share the generator lock"
            operations.rollover_state_override = "ready"
            before = time.perf_counter()
            assert activate(factory, operations, settings) == {
                "outcome": "generated",
                "activated": 25,
            }
            assert time.perf_counter() - before < 2
            assert not running.done()
        finally:
            release.set()
        assert running.result(timeout=5) == {
            "outcome": "pooling",
            "available": 33,
            "added": 8,
        }
    with factory.begin() as db:
        remaining = [
            item["term"] for item in db.get(KeywordCandidatePool, 1).candidates
        ]
        assert remaining == [*prepared[25:], *fresh]
        assert {term.casefold() for term in prepared[:25]} <= set(
            db.scalars(select(KeywordHistory.term))
        )
        assert try_generation_lock(db), "Completed refill leaked its session lock"


def test_concurrent_activators_publish_one_batch(reserve, settings):
    factory, _, prepared = reserve
    barrier, mutex = Barrier(2), Lock()

    class Concurrent(ScraperFake):
        reads = 0

        async def list_keywords(self):
            workspace = await super().list_keywords()
            with mutex:
                self.reads += 1
                first_reads = self.reads <= 2
            if first_reads:
                barrier.wait(timeout=5)
            return workspace

        async def save_keywords(self, payload):
            saved = await super().save_keywords(payload)
            self.rollover_state_override = "working"
            return saved

    operations = Concurrent()
    operations.rollover_enabled = True
    operations.rollover_state_override = "ready"
    with ThreadPoolExecutor(max_workers=2) as executor:
        tasks = [
            executor.submit(activate, factory, operations, settings) for _ in range(2)
        ]
        outcomes = [task.result(timeout=10)["outcome"] for task in tasks]
    assert outcomes.count("generated") == 1
    assert len(operations.keyword_writes) == 1
    with factory() as db:
        assert [
            item["term"] for item in db.get(KeywordCandidatePool, 1).candidates
        ] == prepared[25:]


def test_unexpected_provider_error_releases_generation_lock(reserve, settings):
    factory, operations, _ = reserve

    class Broken(GenerationFake):
        def generate_keywords(self, **kwargs):
            raise RuntimeError("Fixture provider crash")

    with pytest.raises(RuntimeError, match="Fixture provider crash"):
        asyncio.run(
            refill_keyword_reserve(
                factory, settings, operations=operations, provider=Broken()
            )
        )
    with factory.begin() as db:
        assert try_generation_lock(db)
    assert (
        asyncio.run(
            refill_keyword_reserve(
                factory, settings, operations=operations, provider=GenerationFake()
            )
        )["added"]
        == 10
    )
