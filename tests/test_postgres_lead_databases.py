"""Real two-database CSV publication and permanent-history checks."""
import hashlib
import os
import uuid
from pathlib import Path

import psycopg
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.engine import make_url
from psycopg import sql

from conftest import make_request
from jawnix.allocation import allocate_request, inventory_count
from jawnix.billing import BillingError, SubmissionBilling, place_batch_hold, prepare_submission
from jawnix.config import Settings
from jawnix.database import Base
from jawnix.db2_setup import db2_url, provision
from jawnix.job_wakeup import postgres_dsn
from jawnix.lead_uploads import ImportInterrupted, import_lead_upload
from jawnix.inventory_snapshots import REFRESH_INVENTORY_JOB, refresh_inventory_snapshots
from jawnix.models import BatchHold, CreditLedgerEntry, Customer, DistributionEvent, InventorySnapshot, Job, Lead, LeadDatabaseMembership, LeadUpload, ManagedAdmin

DATABASE_URL = os.environ.get("JAWNIX_ACCEPTANCE_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="requires disposable PostgreSQL")


@pytest.fixture
def stores(tmp_path):
    base = make_url(DATABASE_URL)
    assert base.database.endswith("_test"), "Use a disposable *_test database"
    suffix = uuid.uuid4().hex[:16]
    main_name, source_name = f"jawnix_{suffix}_test", f"jawnix_{suffix}_db2_test"
    with psycopg.connect(postgres_dsn(DATABASE_URL), autocommit=True) as admin:
        for name in (main_name, source_name):
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    main_url = base.set(database=main_name).render_as_string(hide_password=False)
    source_url = base.set(database=source_name).render_as_string(hide_password=False)
    settings = Settings(_env_file=None, database_url=main_url, JAWNIX_DB2_DATABASE_URL=source_url, JAWNIX_BATCH_DIR=tmp_path / "batches")
    engine = create_engine(main_url)
    Base.metadata.create_all(engine)
    provision(settings)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory.begin() as db:
        manager = ManagedAdmin(auth_user_id=uuid.uuid4(), name="Alex fixture", email=f"{uuid.uuid4()}@example.test")
        db.add(manager); db.flush(); manager_id=manager.auth_user_id
    try:
        yield settings, factory, manager_id
    finally:
        engine.dispose()
        with psycopg.connect(postgres_dsn(DATABASE_URL), autocommit=True) as admin:
            for name in (main_name, source_name):
                admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def upload(factory, manager_id, path, content):
    path.write_bytes(content)
    with factory.begin() as db:
        item = LeadUpload(manager_id=manager_id, filename=path.name, path=str(path), state="TN", status="queued", bytes=len(content), sha256=hashlib.sha256(content).hexdigest())
        db.add(item); db.flush(); return item.id


@pytest.mark.parametrize("old_rate,new_rate,database", [(500,1000,"db1"),(None,1000,"db1"),(500,1000,"db2")])
def test_reviewed_price_rechecks_committed_rate_and_database(stores, old_rate, new_rate, database):
    _, factory, _ = stores
    with factory.begin() as db:
        customer = Customer(slug=str(uuid.uuid4()), name="Pricing fixture", billing_enabled=old_rate is not None,
                            lead_rate_cents_per_thousand=old_rate)
        db.add(customer); db.flush(); customer_id=customer.id
    with factory() as reviewing:
        stale = reviewing.get(Customer,customer_id)
        assert stale.lead_rate_cents_per_thousand == old_rate
        with factory.begin() as editing:
            current = editing.get(Customer,customer_id)
            current.billing_enabled = True
            current.lead_rate_cents_per_thousand = new_rate
            current.lead_database = database
        with pytest.raises(BillingError,match="price changed"):
            prepare_submission(reviewing,customer_id=customer_id,lead_count=1000,
                               expected_lead_rate_cents_per_thousand=old_rate)
        assert stale.lead_rate_cents_per_thousand == new_rate and stale.lead_database == database


def test_a_database_switch_alone_keeps_the_reviewed_price(stores):
    # Billing belongs to the Customer: moving to DB2 is not a price change.
    _, factory, _ = stores
    with factory.begin() as db:
        customer = Customer(slug=str(uuid.uuid4()), name="Switching fixture", billing_enabled=True,
                            lead_rate_cents_per_thousand=500)
        db.add(customer); db.flush(); customer_id=customer.id
        db.add(CreditLedgerEntry(customer_id=customer_id, kind="admin_adjustment", amount_cents=1000,
                                 reason="Opening credit", actor_user_id="operator"))
    with factory() as reviewing:
        reviewing.get(Customer,customer_id)
        with factory.begin() as editing:
            editing.get(Customer,customer_id).lead_database = "db2"
        billing = prepare_submission(reviewing,customer_id=customer_id,lead_count=1000,
                                     expected_lead_rate_cents_per_thousand=500)
        assert billing == SubmissionBilling(is_billed=True, lead_rate_cents_per_thousand=500, amount_cents=500)


def test_a_billed_db2_batch_is_held_and_captured_against_real_db2_rows(stores, tmp_path):
    settings, factory, owner = stores
    phones = [f"615{uuid.uuid4().int % 10**7:07d}", f"423{uuid.uuid4().int % 10**7:07d}"]
    with factory.begin() as db:
        customer = Customer(slug=str(uuid.uuid4()), name="Billed CSV recipient", lead_database="db2",
                            licensed_states=["TN"], billing_enabled=True, lead_rate_cents_per_thousand=1000)
        db.add(customer); db.flush(); customer_id = customer.id
        db.add(CreditLedgerEntry(customer_id=customer_id, kind="admin_adjustment", amount_cents=100,
                                 reason="Opening credit", actor_user_id="operator"))
    raw = f"Phone,Name\n{phones[0]},First\n{phones[1]},Second\n".encode()
    upload_id = upload(factory, owner, tmp_path / "billed.csv", raw)
    assert import_lead_upload(upload_id, settings, session_factory=factory)["status"] == "complete"
    with factory.begin() as db:
        billing = prepare_submission(db, customer_id=customer_id, lead_count=2,
                                     expected_lead_rate_cents_per_thousand=1000)
        assert billing == SubmissionBilling(is_billed=True, lead_rate_cents_per_thousand=1000, amount_cents=2)
        item = make_request(db, db.get(Customer, customer_id), 2, ["TN"])
        place_batch_hold(db, request=item, billing=billing)
        db.flush(); request_id = item.id
        assert allocate_request(db, request_id, settings).allocated == 2
    with factory() as db:
        events = list(db.scalars(select(DistributionEvent).where(DistributionEvent.request_id == request_id)))
        assert {event.inventory_database for event in events} == {"db2"} and len(events) == 2
        assert db.scalar(select(BatchHold.status).where(BatchHold.request_id == request_id)) == "captured"
        charge = db.scalar(select(CreditLedgerEntry).where(CreditLedgerEntry.batch_request_id == request_id))
        assert (charge.kind, charge.amount_cents) == ("batch_charge", -2)


def test_actual_db2_rows_preserve_db1_data_and_shared_delivery_history(stores, tmp_path):
    settings, factory, owner = stores
    phone = f"615{uuid.uuid4().int % 10**7:07d}"
    second = f"423{uuid.uuid4().int % 10**7:07d}"
    with factory.begin() as db:
        existing = Lead(phone=phone, title="Operator listing", state="TX")
        customer = Customer(slug=str(uuid.uuid4()), name="CSV recipient", lead_database="db2", licensed_states=["TN"])
        db.add_all([existing, customer]); db.flush(); lead_id, customer_id = existing.id, customer.id
    raw = f"Phone,Name,State\n+1{phone},First,TX\n{second},New,CA\n{phone},Latest,WA\ninvalid,Bad,TX\n".encode()
    upload_id = upload(factory, owner, tmp_path / "source.csv", raw)
    result = import_lead_upload(upload_id, settings, session_factory=factory)
    assert result["status"] == "complete"
    assert (result["processedRows"],result["importedRows"],result["duplicateRows"],result["rejectedRows"]) == (4,2,1,1)
    with psycopg.connect(postgres_dsn(db2_url(settings)), autocommit=True) as source:
        rows = source.execute("SELECT phone,title,state FROM inventory_rows WHERE upload_id=%s ORDER BY row_number", (upload_id,)).fetchall()
        assert set(rows) == {(phone,"Latest","TN"),(second,"New","TN")}
        with pytest.raises(psycopg.errors.RaiseException):
            source.execute("UPDATE inventory_rows SET title='Changed' WHERE upload_id=%s", (upload_id,))
        with pytest.raises(psycopg.errors.RaiseException):
            source.execute("INSERT INTO inventory_rows (upload_id,lead_id,phone,title,state,row_number) VALUES (%s,999999999,'2125550100','Late row','TN',99)", (upload_id,))
    with factory.begin() as db:
        assert db.get(Lead,lead_id).title == "Operator listing" and db.get(Lead,lead_id).state == "TX"
        customer = db.get(Customer,customer_id)
        item = make_request(db,customer,2,["TN"])
        assert allocate_request(db,item.id,settings).allocated == 2
        event = db.scalar(select(DistributionEvent).where(DistributionEvent.request_id == item.id, DistributionEvent.lead_id == lead_id))
        assert event.title == "Latest" and event.state == "TN" and event.inventory_database == "db2"
        customer.lead_database = "db1"
        assert inventory_count(db,make_request(db,customer,1,["TX"]),settings) == 0
    # Retrying a complete file creates neither another version nor another publication.
    again = import_lead_upload(upload_id,settings,session_factory=factory)
    assert again["completedAt"] == result["completedAt"]


def test_interrupted_publication_keeps_old_inventory_then_retries_committed_version(stores, tmp_path, monkeypatch):
    settings, factory, owner = stores
    phone = f"865{uuid.uuid4().int % 10**7:07d}"
    first_id = upload(factory,owner,tmp_path / "first.csv",f"Phone,Name\n{phone},First version\n".encode())
    assert import_lead_upload(first_id,settings,session_factory=factory)["status"] == "complete"
    second_id = upload(factory,owner,tmp_path / "second.csv",f"Phone,Name\n{phone},Second version\n".encode())
    from jawnix import lead_uploads
    publish = lead_uploads._publish
    def interrupted(*args):
        raise ImportInterrupted("Fixture publication interrupted")
    monkeypatch.setattr(lead_uploads,"_publish",interrupted)
    with pytest.raises(ImportInterrupted):
        import_lead_upload(second_id,settings,session_factory=factory)
    with factory() as db:
        lead = db.scalar(select(Lead).where(Lead.phone == phone))
        assert db.get(LeadDatabaseMembership,("db2",lead.id)).upload_id == first_id
    monkeypatch.setattr(lead_uploads,"_publish",publish)
    # The physically committed source version is sufficient for recovery.
    (tmp_path / "second.csv").unlink()
    result = import_lead_upload(second_id,settings,session_factory=factory)
    assert result["status"] == "complete" and result["updatedRows"] == 1
    with factory() as db:
        assert db.get(LeadDatabaseMembership,("db2",lead.id)).upload_id == second_id


def test_invalid_csv_never_publishes_a_prefix(stores, tmp_path):
    settings, factory, owner = stores
    phone = f"901{uuid.uuid4().int % 10**7:07d}"
    raw = f"Phone,Name\n{phone},Valid prefix\n".encode()+b"9999999999,\xff\n"
    upload_id = upload(factory,owner,tmp_path / "bad.csv",raw)
    result = import_lead_upload(upload_id,settings,session_factory=factory)
    assert result["status"] == "failed"
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(LeadDatabaseMembership).where(LeadDatabaseMembership.upload_id == upload_id)) == 0


def test_operator_upload_publishes_into_the_shared_db2(stores, tmp_path):
    settings, factory, manager_id = stores
    operator_id = uuid.uuid4()
    phone = f"865{uuid.uuid4().int % 10**7:07d}"
    content = f"Phone,Name\n{phone},Operator row\n".encode()
    path = tmp_path / "operator.csv"
    path.write_bytes(content)
    with factory.begin() as db:
        item = LeadUpload(operator_id=operator_id, filename=path.name, path=str(path), state="TN", status="queued",
                          bytes=len(content), sha256=hashlib.sha256(content).hexdigest())
        db.add(item); db.flush(); upload_id = item.id
    assert import_lead_upload(upload_id, settings, session_factory=factory)["status"] == "complete"
    with psycopg.connect(postgres_dsn(db2_url(settings)), autocommit=True) as source:
        assert source.execute("SELECT owner_id FROM published_inventory WHERE upload_id=%s", (upload_id,)).fetchone() == (operator_id,)
    with factory.begin() as db:
        # Publication queues the usable-inventory recount in its own commit.
        refresh = db.scalar(select(Job).where(Job.kind == REFRESH_INVENTORY_JOB))
        assert refresh is not None and refresh.status == "queued"
        assert refresh.payload == {"reason": "db2_upload"}
        refresh_inventory_snapshots(db)
    with factory() as db:
        snapshot = db.get(InventorySnapshot, "db2")
        assert (snapshot.all_rows, snapshot.usable, snapshot.usable_by_state) == (1, 1, {"TN": 1})
        assert db.scalar(select(LeadDatabaseMembership.upload_id).join(Lead, Lead.id == LeadDatabaseMembership.lead_id).where(Lead.phone == phone)) == upload_id
        with pytest.raises(Exception, match="ck_lead_upload_one_uploader"):
            db.add(LeadUpload(manager_id=manager_id, operator_id=operator_id, filename="both.csv", path="both", state="TN"))
            db.flush()


# The store and link exactly as origin/main's db2_setup provisioned them before
# 0057: no scrubbed_on anywhere, owner_id last in the view.
PRE_0057_STORE = """
CREATE TABLE uploads (
 id uuid PRIMARY KEY, manager_id uuid NOT NULL, state varchar(2) NOT NULL,
 sha256 varchar(64) NOT NULL, status text NOT NULL DEFAULT 'processing',
 processed_rows bigint NOT NULL DEFAULT 0, valid_rows bigint NOT NULL DEFAULT 0,
 rejected_rows bigint NOT NULL DEFAULT 0, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE inventory_rows (
 upload_id uuid NOT NULL REFERENCES uploads(id) ON DELETE RESTRICT,
 lead_id bigint NOT NULL, phone varchar(10) NOT NULL, title text NOT NULL,
 state varchar(2) NOT NULL, row_number bigint NOT NULL,
 PRIMARY KEY (upload_id, lead_id), UNIQUE (upload_id, phone)
);
CREATE OR REPLACE VIEW published_inventory AS
 SELECT r.lead_id, r.upload_id, r.phone, r.title, r.state, r.row_number, u.manager_id AS owner_id
 FROM inventory_rows r JOIN uploads u ON u.id=r.upload_id WHERE u.status='ready';
"""
PRE_0057_FOREIGN_TABLE = """CREATE FOREIGN TABLE db2_inventory_rows (
    lead_id bigint, upload_id uuid, phone varchar(10), title text, state varchar(2),
    row_number bigint, owner_id uuid
) SERVER %s OPTIONS (schema_name 'public', table_name 'published_inventory', updatable 'false')"""


def test_provision_upgrades_a_pre_0057_db2_store(tmp_path):
    """provision() adds scrubbed_on where 0057 needs it and the link still reads."""
    from jawnix.lead_databases import upload_listings

    base = make_url(DATABASE_URL)
    suffix = uuid.uuid4().hex[:16]
    main_name, source_name = f"jawnix_{suffix}_test", f"jawnix_{suffix}_db2_test"
    with psycopg.connect(postgres_dsn(DATABASE_URL), autocommit=True) as admin:
        for name in (main_name, source_name):
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    main_url = base.set(database=main_name).render_as_string(hide_password=False)
    source_url = base.set(database=source_name).render_as_string(hide_password=False)
    settings = Settings(_env_file=None, database_url=main_url, JAWNIX_DB2_DATABASE_URL=source_url, JAWNIX_BATCH_DIR=tmp_path / "batches")
    try:
        with psycopg.connect(postgres_dsn(source_url), autocommit=True) as source:
            source.execute(PRE_0057_STORE)
        with psycopg.connect(postgres_dsn(main_url), autocommit=True) as main:
            main.execute("CREATE EXTENSION IF NOT EXISTS postgres_fdw")
            main.execute(
                sql.SQL("CREATE SERVER {} FOREIGN DATA WRAPPER postgres_fdw OPTIONS (host {}, port {}, dbname {}, fetch_size '5000')").format(
                    sql.Identifier("jawnix_db2_inventory"), sql.Literal("127.0.0.1"),
                    sql.Literal(main.execute("SHOW port").fetchone()[0]), sql.Literal(source_name)))
            main.execute(
                sql.SQL("CREATE USER MAPPING FOR CURRENT_USER SERVER {} OPTIONS (user {}, password {})").format(
                    sql.Identifier("jawnix_db2_inventory"),
                    sql.Literal(make_url(source_url).username), sql.Literal(make_url(source_url).password or "")))
            main.execute(PRE_0057_FOREIGN_TABLE % "jawnix_db2_inventory")
        provision(settings)
        with psycopg.connect(postgres_dsn(source_url), autocommit=True) as source:
            columns = [row[1] for row in source.execute(
                "SELECT ordinal_position, column_name FROM information_schema.columns WHERE table_name='inventory_rows' ORDER BY ordinal_position")]
            assert "scrubbed_on" in columns
            view_columns = [row[0] for row in source.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name='published_inventory' ORDER BY ordinal_position")]
            assert view_columns == ["lead_id", "upload_id", "phone", "title", "state", "row_number", "owner_id", "scrubbed_on"]
            source.execute("INSERT INTO uploads (id,manager_id,state,sha256) VALUES (%s,%s,'TN','abc')",
                           (uuid.uuid4(), uuid.uuid4()))
            upload_id = source.execute("SELECT id FROM uploads").fetchone()[0]
            source.execute("INSERT INTO inventory_rows (upload_id,lead_id,phone,title,state,row_number) VALUES (%s,42,'6155550142','Kept row','TN',1)",
                           (upload_id,))
            source.execute("UPDATE uploads SET status='ready' WHERE id=%s", (upload_id,))
        with psycopg.connect(postgres_dsn(main_url), autocommit=True) as main:
            foreign_columns = [row[0] for row in main.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name='db2_inventory_rows' ORDER BY ordinal_position")]
            assert "scrubbed_on" in foreign_columns
        engine = create_engine(main_url)
        factory = sessionmaker(engine, expire_on_commit=False)
        with factory() as db:
            listing = upload_listings(db, [(42, upload_id)], database="db2")[42]
            assert (listing.phone, listing.title, listing.state) == ("6155550142", "Kept row", "TN")
        engine.dispose()
    finally:
        with psycopg.connect(postgres_dsn(DATABASE_URL), autocommit=True) as admin:
            for name in (main_name, source_name):
                admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
