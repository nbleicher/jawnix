"""Lead lookup (P5): everything about one phone number.

Seam: seeded Leads, deliveries, controls, and Customers in; ``lead_lookup()``
and ``GET /api/admin/leads/lookup`` out. "Who can receive it next" is checked
against allocation itself: a Customer listed as able to receive it now is one
``eligible_query`` would draw it for.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import insert

from jawnix.api import app
from jawnix.auth import Principal, require_admin
from jawnix.config import get_settings
from jawnix.database import get_db
from jawnix.lead_databases import db2_inventory, foreign_metadata
from jawnix.lead_lookup import format_phone, lead_lookup, parse_phone
from jawnix.models import (
    Agency,
    Agent,
    DatasetPublication,
    DistributionEvent,
    EligibilityHold,
    ExclusionList,
    ExclusionPhone,
    Lead,
    LeadCorrectionEvent,
    LeadDatabaseMembership,
    LeadDispositionState,
    LeadDispositionTransition,
    LeadReport,
    LeadRequest,
    LeadUpload,
    ListingObservation,
    ManagedAdmin,
    NichePolicyRow,
    ScraperConfiguration,
    ScraperRun,
)

NOW = datetime(2026, 11, 9, 14, 0, tzinfo=timezone.utc)
PHONE = "6155550142"


def customer(session, name: str, **kwargs) -> Agent:
    item = Agent(
        slug=name.lower().replace(" ", "-"),
        name=name,
        licensed_states=kwargs.pop("states", ["TN"]),
        **kwargs,
    )
    session.add(item)
    session.flush()
    return item


def deliver(session, lead: Lead, recipient: Agent, at: datetime, *, database: str = "db1") -> DistributionEvent:
    request = LeadRequest(
        user_id=uuid.uuid4(),
        agent=recipient,
        lead_count=1,
        states_snapshot=["TN"],
        delivery_email="batch@example.com",
        status="delivered",
        created_at=at,
    )
    session.add(request)
    session.flush()
    event = DistributionEvent(
        lead_id=lead.id,
        customer_id=recipient.id,
        customer_name=recipient.name,
        agency_id=recipient.agency_id,
        agency_name=recipient.agency.name if recipient.agency else "",
        request_id=request.id,
        phone=lead.phone,
        title="Nashville Family Dental",
        state="TN",
        inventory_database=database,
        delivered_at=at,
    )
    session.add(event)
    session.flush()
    return event


def seed(session) -> dict:
    gulf = Agency(slug="gulf-coast", name="Gulf Coast Agency")
    session.add(gulf)
    session.flush()
    harbor = customer(session, "Harbor Insurance", agency_id=gulf.id)
    pelican = customer(session, "Pelican Benefits", agency_id=gulf.id)
    keystone = customer(session, "Keystone Medicare", lead_database="db2")
    summit = customer(session, "Summit Benefits", cooldown_window_days=7)
    ridge = customer(session, "Ridgeline Benefits", cooldown_window_days=1)
    prairie = customer(session, "Prairie State", states=["TX"])
    customer(session, "Former Customer", active=False)
    session.refresh(harbor)

    configuration = ScraperConfiguration(
        version=1, checksum="c" * 64, status="active", anomaly_thresholds={},
        created_by=uuid.uuid4(), reason="Fixture",
    )
    session.add(configuration)
    session.flush()
    run = ScraperRun(
        source="google_maps", source_version="39", configuration_id=configuration.id,
        status="complete",
    )
    session.add(run)
    session.flush()
    session.add(
        DatasetPublication(
            version=39, checksum="d" * 64, scraper_run_id=run.id,
            configuration_id=configuration.id, sync_status="complete",
        )
    )
    lead = Lead(
        phone=PHONE,
        title="Nashville Family Dental",
        state="TN",
        last_distributed_at=NOW - timedelta(days=2),
    )
    session.add(lead)
    session.flush()
    observation = ListingObservation(
        lead_id=lead.id, dataset_checksum="d" * 64, row_number=7,
        normalized_phone=PHONE, title="Nashville Family Dental", state="TN",
        source="TN::dentist", niche="dentist", valid=True,
        observed_at=NOW - timedelta(days=7),
    )
    session.add(observation)
    session.flush()
    lead.current_listing_observation_id = observation.id

    # Managed administration is retired; Alex's uploads still name him.
    manager = ManagedAdmin(auth_user_id=uuid.uuid4(), email="alex@example.com", name="Alex", active=False)
    session.add(manager)
    session.flush()
    upload = LeadUpload(
        manager_id=manager.auth_user_id, filename="tn-agents-nov.csv", state="TN",
        status="complete", path="fixture", created_at=NOW - timedelta(days=3),
        completed_at=NOW - timedelta(days=3),
    )
    session.add(upload)
    session.flush()
    session.add(LeadDatabaseMembership(lead_id=lead.id, upload_id=upload.id, state="TN"))

    older = deliver(session, lead, harbor, NOW - timedelta(days=19))
    newer = deliver(session, lead, keystone, NOW - timedelta(days=2), database="db2")
    transition = LeadDispositionTransition(
        distribution_event_id=older.id, customer_id=harbor.id,
        actor_user_id=uuid.uuid4(), disposition="positive_response",
    )
    session.add(transition)
    session.flush()
    session.add(
        LeadDispositionState(
            distribution_event_id=older.id, current_transition_id=transition.id,
            current_disposition="positive_response", updated_at=NOW,
        )
    )
    session.commit()
    return {
        "lead": lead,
        "upload": upload,
        "harbor": harbor,
        "pelican": pelican,
        "keystone": keystone,
        "summit": summit,
        "ridge": ridge,
        "prairie": prairie,
        "gulf": gulf,
        "older": older,
        "newer": newer,
    }


def rows_by_key(result) -> dict[str, dict]:
    return {row["key"]: row for row in result["nextRecipients"]}


def test_phone_queries_are_recognised_only_when_they_are_a_phone_number():
    assert parse_phone("(615) 555-0142") == PHONE
    assert parse_phone("+1 615.555.0142") == PHONE
    assert parse_phone("615 555 0142") == PHONE
    assert parse_phone("1042") is None
    assert parse_phone("Northstar 6155550142") is None
    assert parse_phone("") is None
    assert format_phone(PHONE) == "(615) 555-0142"


def test_lookup_reads_identity_membership_deliveries_and_listing_history(session, settings):
    records = seed(session)
    result = lead_lookup(session, settings, phone="(615) 555-0142", now=NOW)

    assert result["lead"]["phone"] == PHONE
    assert result["lead"]["title"] == "Nashville Family Dental"
    assert result["lead"]["state"] == "TN"
    assert result["usable"] == {"usable": True, "reasons": []}
    assert result["databases"]["db1"] == {"member": True, "source": "Google Maps listing"}
    db2 = result["databases"]["db2"]
    assert db2["member"] is True and db2["state"] == "TN"
    assert db2["upload"]["filename"] == "tn-agents-nov.csv"
    assert db2["upload"]["uploadedBy"] == "Alex"

    deliveries = result["deliveries"]
    assert [row["customerName"] for row in deliveries] == ["Keystone Medicare", "Harbor Insurance"]
    newest, oldest = deliveries
    assert newest["database"] == "db2" and newest["disposition"] is None
    assert newest["batch"]["href"] == f"/app/admin/batches/{newest['batch']['number']}"
    assert oldest["disposition"] == "Positive response"
    assert oldest["customer"]["href"] == f"/app/admin/customers/{records['harbor'].id}"

    # The SQLite fixture has no DB2 link: the membership still names the upload.
    sources = [row["source"] for row in result["listings"]]
    assert sources == ["DB2 · tn-agents-nov.csv (Alex)", "Run 1 · dentist · TN"]
    assert result["listings"][1]["current"] is True
    assert [action["name"] for action in result["actions"]] == ["correct", "suppress"]
    assert result["evidence"]["kind"] == "current_listing"


def test_db2_listing_titles_come_from_the_published_rows(session, settings):
    records = seed(session)
    foreign_metadata.create_all(session.connection())
    session.execute(
        insert(db2_inventory).values(
            lead_id=records["lead"].id, upload_id=records["upload"].id, phone=PHONE,
            title="Nashville Family Dental LLC", state="TN", row_number=2,
            owner_id=records["upload"].manager_id,
        )
    )
    result = lead_lookup(session, settings, phone=PHONE, now=NOW)
    db2 = [row for row in result["listings"] if row["database"] == "db2"]
    assert db2 == [
        {
            "database": "db2",
            "observedAt": NOW - timedelta(days=3),
            "source": "DB2 · tn-agents-nov.csv (Alex)",
            "title": "Nashville Family Dental LLC",
            "state": "TN",
            "valid": True,
        }
    ]


def test_who_can_receive_it_next_follows_history_cooldown_and_scope(session, settings):
    records = seed(session)
    rows = rows_by_key(lead_lookup(session, settings, phone=PHONE, now=NOW))

    keystone = rows[f"customer:{records['keystone'].id}"]
    harbor = rows[f"customer:{records['harbor'].id}"]
    assert (keystone["status"], keystone["reason"]) == ("never", "delivered")
    assert keystone["at"] == NOW - timedelta(days=2)
    assert (harbor["status"], harbor["reason"]) == ("never", "delivered")

    gulf = rows[f"agency:{records['gulf'].id}"]
    assert gulf["label"] == "Gulf Coast Agency members"
    assert (gulf["status"], gulf["reason"], gulf["customers"]) == (
        "never", "shared_history", ["Pelican Benefits"],
    )

    cooling = [row for row in rows.values() if row["reason"] == "cooldown"]
    assert len(cooling) == 1
    assert cooling[0]["label"] == "Customers with 7-day cooldown"
    assert cooling[0]["customers"] == ["Summit Benefits"]
    assert cooling[0]["at"] == NOW + timedelta(days=5)

    assert rows["now"]["customers"] == ["Ridgeline Benefits"]
    assert rows["not_appointed:TN"]["customers"] == ["Prairie State"]
    # Deactivated Customers cannot ask for a Batch, so they are not listed.
    assert not any("Former Customer" in row["customers"] for row in rows.values())


def test_scope_reasons_name_database_exclusion_and_niche_policy(session, settings):
    records = seed(session)
    session.delete(session.get(LeadDatabaseMembership, records["lead"].id))
    delta = customer(session, "Delta Leads", lead_database="db2")
    careful = customer(session, "Careful Agency", cooldown_window_days=1)
    picky = customer(session, "Picky Benefits", cooldown_window_days=1)
    private = ExclusionList(
        customer_id=careful.id, uploaded_by="customer", exclusion_type="dnc",
        filename="careful.csv", storage_path="x", status="denied",
    )
    session.add(private)
    session.flush()
    session.add(ExclusionPhone(exclusion_list_id=private.id, phone=PHONE))
    session.add(NichePolicyRow(customer_id=picky.id, state=None, mode="only", niche="roofing"))
    session.commit()

    rows = rows_by_key(lead_lookup(session, settings, phone=PHONE, now=NOW))
    assert rows["not_in_database:db2"]["customers"] == ["Delta Leads"]
    assert rows["not_in_database:db2"]["label"] == "DB2 customers"
    assert rows["customer_exclusion"]["customers"] == ["Careful Agency"]
    assert rows["niche_policy"]["customers"] == ["Picky Benefits"]
    assert rows["now"]["customers"] == ["Ridgeline Benefits"]


def test_an_unusable_lead_says_why_and_offers_restore(session, settings):
    records = seed(session)
    lead = records["lead"]
    lead.suppressed = True
    lead.suppression_reason = "Disconnected number"
    report = LeadReport(
        distribution_event_id=records["older"].id, customer_id=records["harbor"].id,
        reason="invalid_phone", details="Rings out",
    )
    session.add(report)
    session.flush()
    session.add(
        EligibilityHold(
            lead_id=lead.id, distribution_event_id=records["older"].id,
            report_id=report.id, reason="invalid_phone",
        )
    )
    dnc = ExclusionList(
        customer_id=None, uploaded_by="admin", exclusion_type="dnc", filename="dnc.csv",
        storage_path="x", status="confirmed", global_effective=True,
    )
    session.add(dnc)
    session.flush()
    session.add(ExclusionPhone(exclusion_list_id=dnc.id, phone=PHONE))
    correction = LeadCorrectionEvent(
        lead_id=lead.id, action="applied", title="Nashville Dental", state="TN",
        actor_id="admin", reason="Renamed", based_on_kind="current_listing",
        based_on_title="Nashville Family Dental", based_on_state="TN",
    )
    session.add(correction)
    session.flush()
    lead.active_correction_id = correction.id
    session.commit()

    result = lead_lookup(session, settings, phone=PHONE, now=NOW)
    assert result["usable"]["usable"] is False
    assert [reason["kind"] for reason in result["usable"]["reasons"]] == [
        "suppressed", "held", "excluded",
    ]
    assert result["usable"]["reasons"][0]["detail"] == "Disconnected number"
    assert result["reports"][0]["reasonLabel"] == "Invalid phone"
    assert result["holds"][0]["active"] is True
    assert result["corrections"][0]["active"] is True
    assert result["exclusions"][0]["filename"] == "dnc.csv"
    assert [action["name"] for action in result["actions"]] == [
        "correct", "remove-correction", "restore",
    ]
    rows = rows_by_key(result)
    assert rows["not_usable"]["customers"] == [
        "Prairie State", "Ridgeline Benefits", "Summit Benefits",
    ]
    assert rows[f"customer:{records['harbor'].id}"]["reason"] == "delivered"


def test_an_unknown_number_still_reports_exclusion_lists(session, settings):
    dnc = ExclusionList(
        customer_id=None, uploaded_by="admin", exclusion_type="dnc", filename="dnc.csv",
        storage_path="x", status="confirmed", global_effective=True,
    )
    session.add(dnc)
    session.flush()
    session.add(ExclusionPhone(exclusion_list_id=dnc.id, phone="2125550100"))
    session.commit()

    result = lead_lookup(session, settings, phone="212-555-0100", now=NOW)
    assert result["lead"] is None
    assert result["valid"] is True
    assert result["phone"] == "2125550100"
    assert [item["filename"] for item in result["exclusions"]] == ["dnc.csv"]

    invalid = lead_lookup(session, settings, phone="call me", now=NOW)
    assert invalid["valid"] is False and invalid["lead"] is None


def test_lookup_endpoint_accepts_a_phone_or_a_lead_id(session):
    records = seed(session)

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=uuid.uuid4(), email="admin@example.com", role="admin", csrf="test"
    )
    try:
        client = TestClient(app)
        by_phone = client.get("/api/admin/leads/lookup", params={"phone": "(615) 555-0142"})
        by_id = client.get("/api/admin/leads/lookup", params={"lead": records["lead"].id})
        missing = client.get("/api/admin/leads/lookup")
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()
    assert by_phone.status_code == 200
    assert by_phone.json()["lead"]["id"] == records["lead"].id
    assert by_id.json()["lead"]["phone"] == PHONE
    assert missing.status_code == 422
