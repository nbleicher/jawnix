"""Usable-inventory snapshots on real PostgreSQL (B4).

Checks the one-pass ``COUNT ... FILTER`` aggregate against the slow reference
rule at volume, reads its query plan, and proves refresh coalescing never
trusts a refresh a worker is already claiming.
"""

from __future__ import annotations

import json
import os
import uuid

import psycopg
import pytest
from psycopg import sql
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from jawnix.database import Base
from jawnix.feedback import apply_disposition_controls
from jawnix.inventory_snapshots import (
    REFRESH_INVENTORY_JOB,
    compute_inventory_snapshot,
    enqueue_inventory_refresh,
    inventory_snapshot_query,
    refresh_inventory_snapshots,
)
from jawnix.job_wakeup import postgres_dsn
from jawnix.jobs import WORKER_LANE_MAINTENANCE, claim_next_job
from jawnix.lead_databases import inventory_scope
from jawnix.models import (
    Agent,
    DistributionEvent,
    ExclusionList,
    InventorySnapshot,
    Job,
    JobStatus,
    Lead,
    LeadDispositionTransition,
    LeadUpload,
)
from jawnix.pool_analytics import _actively_held, _globally_excluded

DATABASE_URL = os.environ.get("JAWNIX_ACCEPTANCE_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires disposable PostgreSQL"
)

ROWS = 60_000


@pytest.fixture
def factory():
    base = make_url(DATABASE_URL)
    assert base.database.endswith("_test"), "Use a disposable *_test database"
    name = f"jawnix_{uuid.uuid4().hex[:16]}_test"
    with psycopg.connect(postgres_dsn(DATABASE_URL), autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    engine = create_engine(
        base.set(database=name).render_as_string(hide_password=False)
    )
    Base.metadata.create_all(engine)
    try:
        yield sessionmaker(engine, expire_on_commit=False)
    finally:
        engine.dispose()
        with psycopg.connect(postgres_dsn(DATABASE_URL), autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
            )


def _hold(db, lead: Lead, customer: Agent) -> None:
    event = DistributionEvent(
        lead_id=lead.id,
        customer_id=customer.id,
        customer_name=customer.name,
        phone=lead.phone,
        title=lead.title,
        state=lead.state,
    )
    db.add(event)
    db.flush()
    transition = LeadDispositionTransition(
        distribution_event_id=event.id,
        customer_id=customer.id,
        actor_user_id=uuid.uuid4(),
        disposition="do_not_contact",
        note="Asked to stop.",
    )
    db.add(transition)
    db.flush()
    apply_disposition_controls(db, event, transition)


def _seed(factory) -> None:
    with factory.begin() as db:
        # Bulk inventory: every 50th suppressed, every 7th also in DB2.
        db.execute(
            text(
                """
                INSERT INTO lead_inventory
                    (phone, in_db1, title, state, first_seen_at, source_flow,
                     legacy_title, legacy_state, suppressed, suppression_reason)
                SELECT lpad((2000000000 + n)::text, 10, '0'), n % 11 <> 0,
                       'Listing ' || n, (ARRAY['TX','FL','PA','OH'])[1 + n % 4],
                       now(), '', '', '', n % 50 = 0, ''
                FROM generate_series(1, :rows) AS n
                """
            ),
            {"rows": ROWS},
        )
        customer = Agent(slug="reporter", name="Reporter")
        upload = LeadUpload(
            operator_id=uuid.uuid4(), filename="db2.csv", path="fixture",
            state="TN", status="complete",
        )
        db.add_all([customer, upload])
        db.flush()
        db.execute(
            text(
                """
                INSERT INTO lead_database_memberships (lead_id, upload_id, state)
                SELECT id, :upload, 'TN' FROM lead_inventory WHERE id % 7 = 0
                """
            ),
            {"upload": upload.id},
        )
        for lead in db.scalars(
            select(Lead).where(Lead.id % 997 == 0).order_by(Lead.id)
        ):
            _hold(db, lead, customer)
        exclusion = ExclusionList(
            customer_id=None, uploaded_by="admin", exclusion_type="dnc",
            filename="dnc.csv", storage_path="/tmp/dnc.csv",
            status="confirmed", global_effective=True,
        )
        scoped = ExclusionList(
            customer_id=customer.id, uploaded_by="customer",
            exclusion_type="dnc", filename="own.csv",
            storage_path="/tmp/own.csv", status="pending_confirmation",
        )
        db.add_all([exclusion, scoped])
        db.flush()
        db.execute(
            text(
                """
                INSERT INTO exclusion_phones (exclusion_list_id, phone)
                SELECT :global, phone FROM lead_inventory WHERE id % 389 = 0
                UNION ALL
                SELECT :scoped, phone FROM lead_inventory WHERE id % 13 = 0
                """
            ),
            {"global": exclusion.id, "scoped": scoped.id},
        )
    with factory() as db:
        db.execute(text("ANALYZE"))
        db.commit()


def test_one_pass_counts_match_the_reference_rule_at_volume(factory):
    _seed(factory)
    with factory() as db:
        for database in ("db1", "db2"):
            counts = compute_inventory_snapshot(db, database)
            reference_all = db.scalar(
                inventory_scope(select(func.count(Lead.id)), database)
            )
            reference_usable = db.scalar(
                inventory_scope(select(func.count(Lead.id)), database).where(
                    Lead.suppressed.is_(False),
                    ~_actively_held(),
                    ~_globally_excluded(),
                )
            )
            assert counts["allRows"] == reference_all
            assert counts["usable"] == reference_usable
            assert counts["held"] > 0 and counts["excluded"] > 0
            assert (
                counts["usable"] + counts["suppressed"] + counts["held"]
                + counts["excluded"]
            ) == counts["allRows"]
            assert sum(counts["usableByState"].values()) == counts["usable"]


def _nodes(plan: dict):
    yield plan
    for child in plan.get("Plans", []):
        yield from _nodes(child)


@pytest.mark.parametrize("database", ["db1", "db2"])
def test_query_plan_reads_inventory_once_without_per_row_subplans(
    factory, database
):
    _seed(factory)
    statement = inventory_snapshot_query(database).compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
    )
    with factory() as db:
        raw = db.execute(text(f"EXPLAIN (FORMAT JSON) {statement}")).scalar()
    plan = (raw if isinstance(raw, list) else json.loads(raw))[0]["Plan"]
    nodes = list(_nodes(plan))
    inventory_scans = [
        node for node in nodes if node.get("Relation Name") == "lead_inventory"
    ]
    # One aggregate pass: lead_inventory is read by exactly one scan node.
    assert len(inventory_scans) == 1
    # Holds and exclusions are joined once, never probed per row.
    assert not any(node.get("Subplan Name") for node in nodes)
    assert not any("SubPlan" in str(node.get("Filter", "")) for node in nodes)
    relations = {node.get("Relation Name") for node in nodes}
    assert {"eligibility_holds", "exclusion_phones", "exclusion_lists"} <= relations
    if database == "db2":
        assert "lead_database_memberships" in relations


def test_refresh_stores_both_snapshots_and_the_pool_breakdown(factory):
    _seed(factory)
    with factory.begin() as db:
        enqueue_inventory_refresh(db, reason="manual")
    with factory.begin() as db:
        result = refresh_inventory_snapshots(db)
    with factory() as db:
        db1 = db.get(InventorySnapshot, "db1")
        db2 = db.get(InventorySnapshot, "db2")
        assert db1.usable == result["db1"]["usable"] > 0
        assert db2.usable == result["db2"]["usable"] > 0
        assert set(db2.usable_by_state) == {"TN"}


def test_coalescing_skips_a_refresh_a_worker_is_claiming(factory):
    with factory.begin() as db:
        waiting = enqueue_inventory_refresh(db, reason="nightly").id

    with factory() as worker:
        # The claim is in flight: its transaction has not committed yet.
        claimed = claim_next_job(
            worker, "worker-1", lane=WORKER_LANE_MAINTENANCE
        )
        assert claimed is not None and claimed.id == waiting
        with factory.begin() as publisher:
            fresh = enqueue_inventory_refresh(publisher, reason="db2_upload").id
            assert fresh != waiting
        worker.commit()

    with factory.begin() as db:
        # A refresh that has not started is reused rather than duplicated.
        assert enqueue_inventory_refresh(db, reason="manual").id == fresh
        statuses = dict(
            db.execute(
                select(Job.id, Job.status).where(
                    Job.kind == REFRESH_INVENTORY_JOB
                )
            ).all()
        )
    assert statuses == {
        waiting: JobStatus.running.value,
        fresh: JobStatus.queued.value,
    }
