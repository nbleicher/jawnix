"""The operator Inventory page (P4) and its tools.

Seam: stored snapshots and small records in; ``GET /api/admin/inventory``, the
usable-by-state CSV, Exclusion Lists, and suppressed Leads out. The page reads
snapshots only: it never counts the inventory.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import event

from jawnix.api import app
from jawnix.auth import Principal, require_admin
from jawnix.database import get_db
from jawnix.inventory import (
    exclusion_lists_overview,
    inventory_overview,
    suppressed_leads_page,
    usable_by_state,
)
from jawnix.inventory_snapshots import store_inventory_snapshot
from jawnix.models import (
    Agent,
    DatasetPublication,
    ExclusionList,
    InventorySyncAttempt,
    Lead,
    LeadDatabaseMembership,
    LeadUpload,
    ManagedAdmin,
    ScraperConfiguration,
    ScraperRun,
)

NOW = datetime(2026, 11, 9, 14, 0, tzinfo=timezone.utc)


def as_admin(session) -> TestClient:
    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=uuid.uuid4(), email="admin@example.com", role="admin", csrf="test"
    )
    return TestClient(app)


def snapshot(session, database: str, usable_by_state: dict[str, int], **counts) -> None:
    usable = sum(usable_by_state.values())
    store_inventory_snapshot(
        session,
        database,
        {
            "allRows": usable + sum(counts.values()),
            "suppressed": counts.get("suppressed", 0),
            "held": counts.get("held", 0),
            "excluded": counts.get("excluded", 0),
            "usable": usable,
            "usableByState": usable_by_state,
        },
        computed_at=NOW - timedelta(hours=8),
    )


def sync(session, version: int, finished: datetime, imported: int) -> None:
    configuration = session.query(ScraperConfiguration).first()
    if configuration is None:
        configuration = ScraperConfiguration(
            version=1, checksum="c" * 64, status="active", anomaly_thresholds={},
            created_by=uuid.uuid4(), reason="Fixture",
        )
        session.add(configuration)
        session.flush()
    run = ScraperRun(
        source="google_maps", source_version=str(version),
        configuration_id=configuration.id, status="complete",
    )
    session.add(run)
    session.flush()
    publication = DatasetPublication(
        version=version, checksum=f"{version:064d}", scraper_run_id=run.id,
        configuration_id=configuration.id, sync_status="complete",
    )
    session.add(publication)
    session.flush()
    session.add(
        InventorySyncAttempt(
            dataset_publication_id=publication.id, dataset_version=version,
            dataset_checksum=publication.checksum, attempt_number=1,
            status="complete", result={"imported": imported}, finished_at=finished,
        )
    )
    session.flush()


def exclusion(session, *, customer_id=None, status="confirmed", created=NOW, **kwargs) -> ExclusionList:
    item = ExclusionList(
        customer_id=customer_id,
        uploaded_by="uploader",
        exclusion_type=kwargs.pop("exclusion_type", "dnc"),
        filename=kwargs.pop("filename", "list.csv"),
        storage_path="x",
        status=status,
        global_effective=status == "confirmed",
        created_at=created,
        **kwargs,
    )
    session.add(item)
    session.flush()
    return item


def seed(session) -> dict:
    snapshot(session, "db1", {"TX": 1_204_002, "FL": 1_011_550, "PA": 980_411}, suppressed=40, held=2, excluded=5)
    snapshot(session, "db2", {"FL": 240_118, "TX": 182_410, "OH": 120_551}, suppressed=3)
    sync(session, 40, NOW - timedelta(days=8), 1_000)
    sync(session, 41, NOW - timedelta(days=2), 18_420)
    sync(session, 42, NOW - timedelta(hours=9), 77_720)
    # Managed administration is retired; Alex's uploads still name him.
    manager = ManagedAdmin(auth_user_id=uuid.uuid4(), email="alex@example.com", name="Alex", active=False)
    session.add(manager)
    session.flush()
    session.add_all(
        [
            LeadUpload(
                manager_id=manager.auth_user_id, filename="tn-agents-nov.csv", state="TN",
                status="complete", path="x", created_at=NOW - timedelta(days=3),
                completed_at=NOW - timedelta(days=3),
            ),
            LeadUpload(
                operator_id=uuid.uuid4(), filename="fl-older.csv", state="FL",
                status="complete", path="x", created_at=NOW - timedelta(days=9),
                completed_at=NOW - timedelta(days=9),
            ),
            LeadUpload(
                operator_id=uuid.uuid4(), filename="oh-importing.csv", state="OH",
                status="processing", path="x", created_at=NOW - timedelta(hours=1),
            ),
        ]
    )
    northstar = Agent(slug="northstar", name="Northstar Insurance")
    session.add(northstar)
    session.flush()
    waiting = exclusion(
        session, customer_id=northstar.id, status="pending_confirmation",
        filename="northstar-dnc.csv", created=NOW - timedelta(days=2),
        accepted_rows=1_200, pool_impact=61,
    )
    exclusion(session, filename="operator-tcpa.csv", exclusion_type="tcpa_litigator", created=NOW - timedelta(days=5))
    suppressed = Lead(phone="6155550142", title="Nashville Family Dental", state="TN", suppressed=True, suppression_reason="Disconnected")
    db2_only = Lead(phone="5125550199", title="", state="", in_db1=False, suppressed=True, suppression_reason="Wrong business")
    session.add_all([suppressed, db2_only, Lead(phone="2125550100", title="Open", state="NY")])
    session.flush()
    upload = session.query(LeadUpload).filter_by(filename="tn-agents-nov.csv").one()
    session.add(LeadDatabaseMembership(lead_id=db2_only.id, upload_id=upload.id, state="TX"))
    session.commit()
    return {"northstar": northstar, "waiting": waiting, "suppressed": suppressed, "db2_only": db2_only}


def test_overview_reads_snapshots_recent_syncs_and_section_counts(session):
    seed(session)
    overview = inventory_overview(session, now=NOW)

    assert overview["db1"]["usable"] == 3_195_963
    assert overview["db1"]["suppressed"] == 40
    assert overview["db2"]["usable"] == 543_079
    assert overview["refresh"] == {"status": "idle", "jobId": None}
    # Two syncs finished inside the week; the one eight days ago did not.
    assert overview["addedLast7Days"] == 96_140
    assert overview["lastUpload"]["filename"] == "tn-agents-nov.csv"
    assert overview["lastUpload"]["uploadedBy"] == "Alex"
    assert overview["sections"] == {
        "uploads": 3,
        "uploadsInProgress": 1,
        "exclusionsWaiting": 1,
        "suppressed": 2,
    }


def test_overview_never_counts_the_inventory(session):
    seed(session)
    statements: list[str] = []

    def record(conn, cursor, statement, *args):
        statements.append(statement.lower())

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        inventory_overview(session, now=NOW)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    inventory_reads = [sql for sql in statements if "from lead_inventory" in sql]
    # Only the suppressed count, which the index on `suppressed` bounds.
    assert len(inventory_reads) == 1
    assert "lead_inventory.suppressed" in inventory_reads[0]


def test_usable_by_state_merges_both_databases_largest_first(session):
    seed(session)
    rows = usable_by_state(session)
    assert [row["state"] for row in rows] == ["TX", "FL", "PA", "OH"]
    assert rows[0] == {"state": "TX", "db1": 1_204_002, "db2": 182_410, "total": 1_386_412}
    assert rows[3] == {"state": "OH", "db1": 0, "db2": 120_551, "total": 120_551}


def test_usable_by_state_csv_downloads_every_state(session):
    seed(session)
    client = as_admin(session)
    try:
        response = client.get("/api/admin/inventory/usable-by-state.csv")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert 'filename="usable-by-state.csv"' in response.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(response.text)))
    assert rows[0] == ["state", "db1_usable", "db2_usable", "total_usable"]
    assert rows[1] == ["TX", "1204002", "182410", "1386412"]
    assert len(rows) == 5


def test_exclusion_lists_put_waiting_lists_first_and_page_the_rest(session):
    records = seed(session)
    overview = exclusion_lists_overview(session, page=1, page_size=1)
    assert [item["filename"] for item in overview["waiting"]] == ["northstar-dnc.csv"]
    waiting = overview["waiting"][0]
    assert waiting["customer"]["href"] == f"/app/admin/customers/{records['northstar'].id}"
    assert waiting["customerName"] == "Northstar Insurance"
    assert waiting["poolImpact"] == 61
    assert overview["lists"]["total"] == 2
    assert overview["lists"]["pages"] == 2
    assert [item["filename"] for item in overview["lists"]["items"]] == ["northstar-dnc.csv"]
    second = exclusion_lists_overview(session, page=2, page_size=1)["lists"]["items"]
    assert second[0]["filename"] == "operator-tcpa.csv"
    assert second[0]["customer"] is None and second[0]["customerName"] == "Operator"
    assert second[0]["global"] is True


def test_suppressed_leads_page_links_lookup_and_offers_restore(session):
    records = seed(session)
    page = suppressed_leads_page(session)
    assert page["total"] == 2
    db2_only, dental = page["items"]
    assert db2_only["id"] == records["db2_only"].id
    assert db2_only["state"] == "TX"
    assert dental["href"] == "/app/admin/inventory/lookup?phone=6155550142"
    assert dental["reason"] == "Disconnected"
    assert [action["name"] for action in dental["actions"]] == ["restore"]


def test_inventory_endpoints_answer_for_the_operator(session):
    seed(session)
    client = as_admin(session)
    try:
        overview = client.get("/api/admin/inventory").json()
        exclusions = client.get("/api/admin/exclusion-lists").json()
        suppressed = client.get("/api/admin/suppressed-leads", params={"page": 9}).json()
    finally:
        app.dependency_overrides.clear()
    assert overview["sections"]["exclusionsWaiting"] == 1
    assert len(exclusions["waiting"]) == 1
    # A page past the end lands on the last page rather than an empty one.
    assert suppressed["page"] == 1 and len(suppressed["items"]) == 2


def test_activity_links_moved_records_to_inventory(session):
    from jawnix.activity import query_activity, record_activity

    for target_type, target_id in (
        ("lead", 92),
        ("lead_upload", uuid.uuid4()),
        ("exclusion_list", uuid.uuid4()),
        ("niche_assignment_upload", uuid.uuid4()),
    ):
        record_activity(
            session,
            action=f"{target_type}_changed",
            target_type=target_type,
            target_id=target_id,
            actor_id="admin",
            reason="Fixture",
        )
    session.commit()
    hrefs = {
        entry["entityType"]: entry["entityHref"]
        for entry in query_activity(session, page=1, page_size=10)["entries"]
    }
    assert hrefs == {
        "lead": "/app/admin/inventory/lookup?lead=92",
        "lead_upload": "/app/admin/inventory/uploads",
        "exclusion_list": "/app/admin/inventory/exclusions",
        "niche_assignment_upload": "/app/admin/inventory/niche-assignment",
    }
