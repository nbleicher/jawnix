import uuid
import pytest
from datetime import datetime, timedelta, timezone

from sqlalchemy import insert, select

from conftest import make_request
from jawnix.allocation import allocate_request, inventory_count
from jawnix.lead_databases import db2_inventory, foreign_metadata
from jawnix.models import Customer, DistributionEvent, Lead, LeadDatabaseMembership, LeadUpload, ManagedAdmin


def test_db2_rejects_the_same_database_with_an_implicit_default_port():
    from jawnix.config import Settings
    from jawnix.db2_setup import provision
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://fixture@localhost:5432/primary",
                        JAWNIX_DB2_DATABASE_URL="postgresql+psycopg://fixture@localhost/primary")
    with pytest.raises(ValueError, match="physically separate"):
        provision(settings)


def db2_source(session, leads, state="TN"):
    foreign_metadata.create_all(session.connection())
    manager = ManagedAdmin(auth_user_id=uuid.uuid4(), name="Alex", email=f"{uuid.uuid4()}@example.test")
    session.add(manager); session.flush()
    upload = LeadUpload(manager_id=manager.auth_user_id, filename="source.csv", path="fixture", state=state, status="complete")
    session.add(upload); session.flush()
    for index, lead in enumerate(leads):
        session.add(LeadDatabaseMembership(lead_id=lead.id, upload_id=upload.id, state=state))
        session.execute(insert(db2_inventory).values(lead_id=lead.id, upload_id=upload.id, phone=lead.phone,
                                                   title=f"CSV {index}", state=state, row_number=index+2, owner_id=manager.auth_user_id))
    session.flush()
    return upload


def test_switching_databases_keeps_permanent_no_repeat_and_exact_quantity(session, settings):
    customer = Customer(slug="recipient", name="Recipient", licensed_states=["TN"])
    shared = Lead(phone="6155550001", title="Operator listing", state="TN")
    csv_only = Lead(phone="6155550002", title="", state="", in_db1=False)
    session.add_all([customer, shared, csv_only]); session.flush()
    db2_source(session, [shared, csv_only])
    first = make_request(session, customer, 1, ["TN"])
    assert allocate_request(session, first.id, settings).allocated == 1
    event = session.scalar(select(DistributionEvent).where(DistributionEvent.request_id == first.id))
    assert event.phone == shared.phone and event.title == "Operator listing" and event.inventory_database == "db1"
    customer.lead_database = "db2"
    short = make_request(session, customer, 2, ["TN"])
    result = allocate_request(session, short.id, settings)
    assert result.status == "waiting_inventory" and result.allocated == 0 and result.available == 1
    assert list(session.scalars(select(DistributionEvent).where(DistributionEvent.request_id == short.id))) == []
    second = make_request(session, customer, 1, ["TN"])
    assert allocate_request(session, second.id, settings).allocated == 1
    delivered = session.scalar(select(DistributionEvent).where(DistributionEvent.request_id == second.id))
    assert delivered.phone == csv_only.phone and delivered.title == "CSV 1" and delivered.state == "TN"
    assert delivered.inventory_database == "db2" and delivered.listing_provenance["kind"] == "csv_upload"
    assert csv_only.title == "" and csv_only.state == "" and not csv_only.in_db1
    assert event.title == "Operator listing" and event.inventory_database == "db1"


def test_global_cooldown_and_phone_alias_history_survive_source_switch(session, settings):
    first = Customer(slug="first", name="First", licensed_states=["TN"], lead_database="db2")
    second = Customer(slug="second", name="Second", licensed_states=["TN"], lead_database="db2")
    lead = Lead(phone="6155550001", title="DB1", state="TN", last_distributed_at=datetime.now(timezone.utc))
    session.add_all([first, second, lead]); session.flush()
    db2_source(session, [lead])
    session.add(DistributionEvent(lead_id=lead.id, agent_id=first.id, phone=lead.phone, title="Old DB1", state="TN", delivered_at=lead.last_distributed_at))
    session.flush()
    assert inventory_count(session, make_request(session, first, 1, ["TN"]), settings) == 0
    assert inventory_count(session, make_request(session, second, 1, ["TN"]), settings) == 0


def test_db2_state_is_independent_of_db1_listing_state(session, settings):
    customer = Customer(slug="csv", name="CSV", licensed_states=["TN"], lead_database="db2")
    lead = Lead(phone="6155550001", title="DB1 title", state="TX")
    session.add_all([customer, lead]); session.flush()
    db2_source(session, [lead], "TN")
    item = make_request(session, customer, 1, ["TN"])
    assert allocate_request(session, item.id, settings).allocated == 1
    event = session.scalar(select(DistributionEvent).where(DistributionEvent.request_id == item.id))
    assert event.title == "CSV 0" and event.state == "TN"
    assert lead.title == "DB1 title" and lead.state == "TX"


def test_db2_state_specific_niche_policy_uses_uploaded_state(session, settings):
    from jawnix.models import NichePolicyRow
    customer = Customer(slug="policy", name="Policy", licensed_states=["TN"], lead_database="db2")
    lead = Lead(phone="6155550001", title="DB1", state="TX")
    session.add_all([customer, lead]); session.flush()
    db2_source(session, [lead], "TN")
    session.add(NichePolicyRow(customer_id=customer.id, state="TN", mode="only", niche="Roofing")); session.flush()
    item = make_request(session,customer,1,["TN"])
    assert inventory_count(session,item,settings) == 0
    assert inventory_count(session,item,settings,niche_policy_rows=[{"state":"TN","mode":"only","niches":["Roofing"]}]) == 0


def test_state_only_correction_preserves_csv_name_and_overrides_source_state(session, settings):
    from jawnix.eligibility import record_correction
    customer = Customer(slug="correction", name="Correction", licensed_states=["TX"], lead_database="db2")
    lead = Lead(phone="6155550001", title="", state="", in_db1=False)
    session.add_all([customer,lead]); session.flush()
    db2_source(session,[lead],"TN")
    correction = record_correction(session,lead,actor_id="fixture",reason="Correct state",title=None,state="TX")
    assert correction.title == "CSV 0" and correction.based_on_kind == "csv_upload"
    item = make_request(session,customer,1,["TX"])
    assert allocate_request(session,item.id,settings).allocated == 1
    event = session.scalar(select(DistributionEvent).where(DistributionEvent.request_id == item.id))
    assert event.title == "CSV 0" and event.state == "TX"
