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
from jawnix.billing import BillingError, prepare_submission
from jawnix.config import Settings
from jawnix.database import Base
from jawnix.db2_setup import db2_url, provision
from jawnix.job_wakeup import postgres_dsn
from jawnix.lead_uploads import ImportInterrupted, import_lead_upload
from jawnix.models import Customer, DistributionEvent, Lead, LeadDatabaseMembership, LeadUpload, ManagedAdmin

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


@pytest.mark.parametrize("old_rate,new_rate,database", [(500,1000,"db1"),(None,1000,"db1"),(500,500,"db2")])
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
        assert db.get(LeadDatabaseMembership,lead.id).upload_id == first_id
    monkeypatch.setattr(lead_uploads,"_publish",publish)
    # The physically committed source version is sufficient for recovery.
    (tmp_path / "second.csv").unlink()
    result = import_lead_upload(second_id,settings,session_factory=factory)
    assert result["status"] == "complete" and result["updatedRows"] == 1
    with factory() as db:
        assert db.get(LeadDatabaseMembership,lead.id).upload_id == second_id


def test_invalid_csv_never_publishes_a_prefix(stores, tmp_path):
    settings, factory, owner = stores
    phone = f"901{uuid.uuid4().int % 10**7:07d}"
    raw = f"Phone,Name\n{phone},Valid prefix\n".encode()+b"9999999999,\xff\n"
    upload_id = upload(factory,owner,tmp_path / "bad.csv",raw)
    result = import_lead_upload(upload_id,settings,session_factory=factory)
    assert result["status"] == "failed"
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(LeadDatabaseMembership).where(LeadDatabaseMembership.upload_id == upload_id)) == 0
