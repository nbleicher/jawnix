"""Clean (DB3) inventory against real two-database PostgreSQL: plan T1-T2, T4, T6-T9."""
import hashlib
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from conftest import make_request
from jawnix.allocation import allocate_request, fulfill_round_robin, inventory_count
from jawnix.billing import BillingError, SubmissionBilling, place_batch_hold, prepare_submission
from jawnix.clean_inventory import PROMOTE_DB2_ONLY_LEADS_ON_CLEAN_EXPIRY, expire_clean_inventory
from jawnix.config import get_settings
from jawnix.lead_databases import CLEAN_SHELF_DAYS
from jawnix.lead_uploads import import_lead_upload
from jawnix.models import (
    AuditEntry,
    CreditLedgerEntry,
    Customer,
    CustomerAvailabilitySnapshot,
    DistributionEvent,
    Job,
    Lead,
    LeadDatabaseMembership,
    LeadDatabaseSummary,
    LeadRequest,
    LeadUpload,
    RequestStatus,
)
from jawnix.pool_analytics import refresh_customer_availability

from test_postgres_lead_databases import DATABASE_URL, stores, upload


pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="requires disposable PostgreSQL")
assert stores is not None  # fixture import


def upload3(factory, owner, path, content, *, scrubbed_on=None):
    path.write_bytes(content)
    with factory.begin() as db:
        item = LeadUpload(
            manager_id=owner,
            filename=path.name,
            path=str(path),
            state="TN",
            status="queued",
            bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            inventory_database="db3",
            scrubbed_on=scrubbed_on or date.today(),
        )
        db.add(item)
        db.flush()
        return item.id


def clean_request(db, customer: Customer, count: int, states: list[str]) -> object:
    item = make_request(db, customer, count, states)
    item.clean = True
    db.flush()
    return item


def customer(db, name: str, *, database: str = "db1", states=("TN",)) -> Customer:
    item = Customer(slug=str(uuid.uuid4()), name=name, lead_database=database, licensed_states=list(states))
    db.add(item)
    db.flush()
    return item


def phone() -> str:
    return f"615{uuid.uuid4().int % 10**7:07d}"


def csv_rows(*rows: tuple[str, str]) -> bytes:
    return ("Phone,Name\n" + "".join(f"{number},{name}\n" for number, name in rows)).encode()


def membership(db, database: str, lead_id: int) -> LeadDatabaseMembership | None:
    return db.get(LeadDatabaseMembership, (database, lead_id))


@pytest.fixture
def clean_rate(monkeypatch):
    monkeypatch.setenv("JAWNIX_CLEAN_LEAD_RATE_CENTS_PER_THOUSAND", "500")
    get_settings.cache_clear()
    yield 500
    get_settings.cache_clear()


def test_a_clean_request_picks_only_db3_and_freezes_the_clean_rate(stores, tmp_path, clean_rate):
    """T3: Clean draws DB3 at 500 cents per 1,000, frozen on the request."""
    settings, factory, owner = stores
    clean_phones = [phone(), phone()]
    with factory.begin() as db:
        db.add(Lead(phone=phone(), title="Standard only", state="TN", in_db1=True))
        buyer = Customer(slug=str(uuid.uuid4()), name="Clean buyer", licensed_states=["TN"],
                         billing_enabled=True, lead_rate_cents_per_thousand=150)
        db.add(buyer)
        db.flush()
        customer_id = buyer.id
        db.add(CreditLedgerEntry(customer_id=customer_id, kind="admin_adjustment", amount_cents=10_000,
                                 reason="Opening credit", actor_user_id="operator"))
    upload_id = upload3(factory, owner, tmp_path / "clean.csv",
                        csv_rows((clean_phones[0], "First"), (clean_phones[1], "Second")))
    assert import_lead_upload(upload_id, settings, session_factory=factory)["status"] == "complete"
    with factory.begin() as db:
        billing = prepare_submission(db, customer_id=customer_id, lead_count=2, clean=True,
                                     expected_lead_rate_cents_per_thousand=500)
        assert billing == SubmissionBilling(is_billed=True, lead_rate_cents_per_thousand=500, amount_cents=1)
        item = clean_request(db, db.get(Customer, customer_id), 2, ["TN"])
        place_batch_hold(db, request=item, billing=billing)
        db.flush()
        assert allocate_request(db, item.id, settings).allocated == 2
        request_id = item.id
    with factory() as db:
        item = db.get(LeadRequest, request_id)
        assert item.clean and item.lead_rate_cents_per_thousand == 500
        events = list(db.scalars(select(DistributionEvent).where(DistributionEvent.request_id == request_id)))
        assert {event.inventory_database for event in events} == {"db3"}
        assert {event.phone for event in events} == set(clean_phones)
        charge = db.scalar(select(CreditLedgerEntry).where(CreditLedgerEntry.batch_request_id == request_id))
        assert (charge.kind, charge.amount_cents) == ("batch_charge", -1)


def test_a_clean_request_is_refused_without_clean_billing_or_the_rate(stores, tmp_path, monkeypatch):
    """T5: Clean off, an unbilled customer, or a stale expected rate all refuse."""
    settings, factory, _ = stores
    with factory.begin() as db:
        billed = Customer(slug=str(uuid.uuid4()), name="Billed", licensed_states=["TN"],
                          billing_enabled=True, lead_rate_cents_per_thousand=150)
        unbilled = Customer(slug=str(uuid.uuid4()), name="Unbilled", licensed_states=["TN"])
        db.add_all([billed, unbilled])
        db.flush()
        billed_id, unbilled_id = billed.id, unbilled.id
        db.add(CreditLedgerEntry(customer_id=billed_id, kind="admin_adjustment", amount_cents=10_000,
                                 reason="Opening credit", actor_user_id="operator"))
    # Clean is off while the rate is unset.
    monkeypatch.delenv("JAWNIX_CLEAN_LEAD_RATE_CENTS_PER_THOUSAND", raising=False)
    get_settings.cache_clear()
    with factory.begin() as db:
        with pytest.raises(BillingError, match="not on sale"):
            prepare_submission(db, customer_id=billed_id, lead_count=1000, clean=True,
                               expected_lead_rate_cents_per_thousand=500)
    monkeypatch.setenv("JAWNIX_CLEAN_LEAD_RATE_CENTS_PER_THOUSAND", "500")
    get_settings.cache_clear()
    with factory.begin() as db:
        with pytest.raises(BillingError, match="billing"):
            prepare_submission(db, customer_id=unbilled_id, lead_count=1000, clean=True,
                               expected_lead_rate_cents_per_thousand=500)
        with pytest.raises(BillingError, match="price changed"):
            prepare_submission(db, customer_id=billed_id, lead_count=1000, clean=True,
                               expected_lead_rate_cents_per_thousand=150)
        # The customer's own rate is untouched by the Clean rate.
        standard = prepare_submission(db, customer_id=billed_id, lead_count=1000,
                                      expected_lead_rate_cents_per_thousand=150)
        assert standard.lead_rate_cents_per_thousand == 150
    get_settings.cache_clear()


def test_a_db3_upload_leaves_db1_and_db2_status_unchanged(stores, tmp_path):
    """T1: the same phone keeps its DB1 row and its DB2 membership beside DB3."""
    settings, factory, owner = stores
    in_db1, in_db2, fresh = phone(), phone(), phone()
    with factory.begin() as db:
        first = Lead(phone=in_db1, title="Operator listing", state="TX", in_db1=True)
        second = Lead(phone=in_db2, title="", state="", in_db1=False, source_flow="db2_registry")
        db.add_all([first, second])
        db.flush()
        first_id, second_id = first.id, second.id
    db2_upload_id = upload(factory, owner, tmp_path / "db2.csv", csv_rows((in_db2, "CSV listing")))
    assert import_lead_upload(db2_upload_id, settings, session_factory=factory)["status"] == "complete"
    db3_upload_id = upload3(factory, owner, tmp_path / "db3.csv", csv_rows(
        (in_db1, "Scrubbed one"), (in_db2, "Scrubbed two"), (fresh, "Scrubbed three"),
    ))
    result = import_lead_upload(db3_upload_id, settings, session_factory=factory)
    assert result["status"] == "complete" and result["rejectedRows"] == 0
    with factory() as db:
        lead = db.get(Lead, first_id)
        assert lead.in_db1 and (lead.title, lead.state) == ("Operator listing", "TX")
        other = db.get(Lead, second_id)
        assert not other.in_db1
        assert membership(db, "db2", second_id).upload_id == db2_upload_id
        assert membership(db, "db3", second_id).upload_id == db3_upload_id
        clean = db.scalar(select(Lead).where(Lead.phone == fresh))
        assert not clean.in_db1 and membership(db, "db2", clean.id) is None
        scrubbed = membership(db, "db3", clean.id)
        assert scrubbed.expires_on == date.today() + timedelta(days=CLEAN_SHELF_DAYS)
        summaries = {
            row.id: row.total
            for row in db.scalars(select(LeadDatabaseSummary))
        }
        assert summaries == {"db2": 1, "db3": 3}


def test_expired_db3_rows_are_never_picked(stores, tmp_path):
    """T2: a row past its shelf life fails the pick-time filter even before the sweep."""
    settings, factory, owner = stores
    old, current = phone(), phone()
    old_upload = upload3(factory, owner, tmp_path / "old.csv", csv_rows((old, "Stale")),
                         scrubbed_on=date.today() - timedelta(days=CLEAN_SHELF_DAYS + 1))
    assert import_lead_upload(old_upload, settings, session_factory=factory)["status"] == "complete"
    new_upload = upload3(factory, owner, tmp_path / "new.csv", csv_rows((current, "Fresh")))
    assert import_lead_upload(new_upload, settings, session_factory=factory)["status"] == "complete"
    with factory.begin() as db:
        buyer = customer(db, "Clean buyer")
        item = clean_request(db, buyer, 2, ["TN"])
        db.flush()
        result = allocate_request(db, item.id, settings)
        assert result.status == RequestStatus.waiting_inventory.value
        assert result.available == 1
        event_phones = set(db.scalars(select(DistributionEvent.phone)))
        assert old not in event_phones


def test_delivery_history_holds_across_all_three_databases(stores, tmp_path):
    """T4: a DB1 or DB2 sale blocks the Clean sale, and cooldown blocks everywhere."""
    settings, factory, owner = stores
    sold, clean_only = phone(), phone()
    with factory.begin() as db:
        db.add(Lead(phone=sold, title="Standard listing", state="TN", in_db1=True))
        first = customer(db, "First buyer")
        second = customer(db, "Second buyer")
        db.flush()
        first_id, second_id = first.id, second.id
        standard = make_request(db, first, 1, ["TN"])
        db.flush()
        assert allocate_request(db, standard.id, settings).allocated == 1
    upload3_id = upload3(factory, owner, tmp_path / "db3.csv", csv_rows((sold, "Scrubbed"), (clean_only, "Clean only")))
    assert import_lead_upload(upload3_id, settings, session_factory=factory)["status"] == "complete"
    with factory.begin() as db:
        # Rule 1: the same customer never gets the phone again, from any database.
        repeat = clean_request(db, db.get(Customer, first_id), 1, ["TN"])
        assert inventory_count(db, repeat, settings) == 1
        # Rule 2: another customer is blocked by the Cooldown Window too.
        cooling = clean_request(db, db.get(Customer, second_id), 1, ["TN"])
        assert inventory_count(db, cooling, settings) == 1
        events = db.scalars(select(DistributionEvent).order_by(DistributionEvent.id)).all()
        assert len(events) == 1 and events[0].phone == sold and events[0].inventory_database == "db1"
    # Once the Cooldown Window lapses, the second customer can buy it Clean.
    with factory.begin() as db:
        event = db.scalar(select(DistributionEvent))
        event.delivered_at = datetime.now(timezone.utc) - timedelta(days=8)
        lead = db.scalar(select(Lead).where(Lead.phone == sold))
        lead.last_distributed_at = event.delivered_at
    with factory.begin() as db:
        # The previously sold phone is eligible again, so both DB3 rows count.
        later = clean_request(db, db.get(Customer, second_id), 1, ["TN"])
        assert inventory_count(db, later, settings) == 2
        assert allocate_request(db, later.id, settings).allocated == 1
        event = db.scalar(select(DistributionEvent).where(DistributionEvent.request_id == later.id))
        assert event.inventory_database == "db3" and event.phone in {sold, clean_only}
    # And a Clean sale blocks DB1 and DB2 in turn.
    with factory.begin() as db:
        blocked = make_request(db, db.get(Customer, first_id), 1, ["TN"])
        assert inventory_count(db, blocked, settings) == 0


def test_a_short_clean_request_fills_after_a_db3_upload(stores, tmp_path):
    """T6: waiting_inventory, then the publication's fulfill turn completes it."""
    settings, factory, owner = stores
    first_two = [(phone(), f"Row {index}") for index in range(2)]
    upload_id = upload3(factory, owner, tmp_path / "first.csv", csv_rows(*first_two))
    assert import_lead_upload(upload_id, settings, session_factory=factory)["status"] == "complete"
    with factory.begin() as db:
        buyer = customer(db, "Clean buyer")
        item = clean_request(db, buyer, 4, ["TN"])
        db.flush()
        request_id = item.id
        result = allocate_request(db, request_id, settings)
        assert result.status == RequestStatus.waiting_inventory.value and result.available == 2
    next_two = [(phone(), f"Later {index}") for index in range(2)]
    second_id = upload3(factory, owner, tmp_path / "second.csv", csv_rows(*next_two))
    assert import_lead_upload(second_id, settings, session_factory=factory)["status"] == "complete"
    with factory.begin() as db:
        outcome = fulfill_round_robin(db, settings)
        assert outcome["requestsFulfilled"] == 1
        item = db.get(LeadRequest, request_id)
        assert item.status == RequestStatus.generated.value
        assert db.scalar(select(func.count()).select_from(DistributionEvent).where(DistributionEvent.request_id == request_id)) == 4
        assert {event.inventory_database for event in db.scalars(select(DistributionEvent).where(DistributionEvent.request_id == request_id))} == {"db3"}


def test_expiry_moves_db3_only_phones_to_db1_and_drops_the_rest(stores, tmp_path):
    """T7: the nightly sweep, and DB1 batch CSVs keep their columns."""
    settings, factory, owner = stores
    assert PROMOTE_DB2_ONLY_LEADS_ON_CLEAN_EXPIRY is False
    only_clean, in_db1, in_db2 = phone(), phone(), phone()
    with factory.begin() as db:
        db.add(Lead(phone=in_db1, title="Operator listing", state="TX", in_db1=True))
        db.flush()
    db2_upload = upload(factory, owner, tmp_path / "db2.csv", csv_rows((in_db2, "CSV listing")))
    assert import_lead_upload(db2_upload, settings, session_factory=factory)["status"] == "complete"
    db3_upload = upload3(factory, owner, tmp_path / "db3.csv", csv_rows(
        (only_clean, "Clean one"), (in_db1, "Clean two"), (in_db2, "Clean three"),
    ))
    assert import_lead_upload(db3_upload, settings, session_factory=factory)["status"] == "complete"
    with factory.begin() as db:
        # The DB1 batch CSV layout is untouched by any of this.
        buyer = customer(db, "Standard buyer", states=("TX",))
        item = make_request(db, buyer, 1, ["TX"])
        db.flush()
        assert allocate_request(db, item.id, settings).allocated == 1
        artifact = item.artifact
        import zipfile
        with zipfile.ZipFile(artifact.path) as bundle:
            with bundle.open(bundle.namelist()[0]) as part:
                assert part.readline().decode().strip() == "phone,title,state"
    sweep_day = date.today() + timedelta(days=CLEAN_SHELF_DAYS + 1)
    with factory.begin() as db:
        counts = expire_clean_inventory(db, settings, today=sweep_day)
        assert counts == {"expired": 3, "movedToDb1": 1}
    with factory() as db:
        moved = db.scalar(select(Lead).where(Lead.phone == only_clean))
        assert moved.in_db1 and (moved.title, moved.state) == ("Clean one", "TN")
        kept = db.scalar(select(Lead).where(Lead.phone == in_db1))
        assert kept.in_db1 and (kept.title, kept.state) == ("Operator listing", "TX")
        db2_only = db.scalar(select(Lead).where(Lead.phone == in_db2))
        # D15 as assumed: a phone whose other inventory is DB2 is not added to DB1.
        assert not db2_only.in_db1
        for lead in (moved, kept, db2_only):
            assert membership(db, "db3", lead.id) is None
        assert membership(db, "db2", db2_only.id) is not None
        entry = db.scalar(select(AuditEntry).where(AuditEntry.action == "clean_inventory_expired"))
        assert entry.details == {"expired": 3, "movedToDb1": 1, "asOf": sweep_day.isoformat()}
        assert db.scalar(select(Job).where(Job.kind == "refresh_inventory_snapshots")) is not None


def test_a_phone_moved_into_db1_keeps_its_delivery_history(stores, tmp_path):
    """T8: expiry into DB1 resets neither the permanent no-repeat nor the cooldown."""
    settings, factory, owner = stores
    sold = phone()
    upload_id = upload3(factory, owner, tmp_path / "db3.csv", csv_rows((sold, "Clean listing")))
    assert import_lead_upload(upload_id, settings, session_factory=factory)["status"] == "complete"
    with factory.begin() as db:
        first = customer(db, "Clean buyer")
        second = customer(db, "Later buyer")
        db.flush()
        first_id, second_id = first.id, second.id
        item = clean_request(db, first, 1, ["TN"])
        db.flush()
        assert allocate_request(db, item.id, settings).allocated == 1
        event = db.scalar(select(DistributionEvent))
        assert event.inventory_database == "db3"
    with factory.begin() as db:
        row = db.scalar(select(LeadDatabaseMembership))
        lead = db.get(Lead, row.lead_id)
        lead_id = lead.id
        counts = expire_clean_inventory(db, settings, today=row.expires_on)
        assert counts["movedToDb1"] == 1
        assert lead.in_db1 and (lead.title, lead.state) == ("Clean listing", "TN")
    with factory.begin() as db:
        # Rule 1: the Clean buyer never gets it again, now from DB1.
        repeat = make_request(db, db.get(Customer, first_id), 1, ["TN"])
        assert inventory_count(db, repeat, settings) == 0
        # Rule 2: everyone else waits out the Cooldown Window from the DB3 sale.
        cooling = make_request(db, db.get(Customer, second_id), 1, ["TN"])
        assert inventory_count(db, cooling, settings) == 0
    with factory.begin() as db:
        event = db.scalar(select(DistributionEvent))
        event.delivered_at = datetime.now(timezone.utc) - timedelta(days=8)
        db.get(Lead, lead_id).last_distributed_at = event.delivered_at
    with factory.begin() as db:
        later = make_request(db, db.get(Customer, second_id), 1, ["TN"])
        assert inventory_count(db, later, settings) == 1


def test_availability_snapshots_are_keyed_per_database(stores, tmp_path):
    """T9: a DB3 publication clears Clean availability and leaves Standard alone."""
    settings, factory, owner = stores
    with factory.begin() as db:
        buyer = customer(db, "CSV buyer", database="db2")
        db.flush()
        customer_id = buyer.id
        refresh_customer_availability(db, buyer, settings)
    with factory() as db:
        assert db.get(CustomerAvailabilitySnapshot, (customer_id, "db2")) is not None
        assert db.get(CustomerAvailabilitySnapshot, (customer_id, "db3")) is not None
    db3_upload = upload3(factory, owner, tmp_path / "db3.csv", csv_rows((phone(), "Clean")))
    assert import_lead_upload(db3_upload, settings, session_factory=factory)["status"] == "complete"
    with factory() as db:
        assert db.get(CustomerAvailabilitySnapshot, (customer_id, "db3")) is None
        assert db.get(CustomerAvailabilitySnapshot, (customer_id, "db2")) is not None
    with factory.begin() as db:
        refresh_customer_availability(db, db.get(Customer, customer_id), settings)
    db2_upload = upload(factory, owner, tmp_path / "db2.csv", csv_rows((phone(), "Standard")))
    assert import_lead_upload(db2_upload, settings, session_factory=factory)["status"] == "complete"
    with factory() as db:
        assert db.get(CustomerAvailabilitySnapshot, (customer_id, "db2")) is None
        assert db.get(CustomerAvailabilitySnapshot, (customer_id, "db3")) is not None
