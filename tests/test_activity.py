from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from jawnix.activity import (
    UnsafeActivityDetailsError,
    query_activity,
    record_activity,
)
from jawnix.api import app
from jawnix.auth import Principal, require_admin, require_principal
from jawnix.config import get_settings
from jawnix.database import get_db
from jawnix.models import AuditEntry


def test_record_activity_rejects_secret_keys_and_known_material(session):
    known_secret = "known-secret-material-76"

    with pytest.raises(
        UnsafeActivityDetailsError,
        match="secret-bearing key",
    ):
        record_activity(
            session,
            action="user_account_created",
            target_type="user_account",
            target_id=uuid.uuid4(),
            actor_id=uuid.uuid4(),
            reason="Create an account.",
            details={"after": {"accessToken": known_secret}},
        )

    with pytest.raises(
        UnsafeActivityDetailsError,
        match="known secret material",
    ):
        record_activity(
            session,
            action="user_account_created",
            target_type="user_account",
            target_id=uuid.uuid4(),
            actor_id=uuid.uuid4(),
            reason="Create an account.",
            details={"after": {"opaqueValue": known_secret}},
            known_secrets=(known_secret,),
        )

    assert session.query(AuditEntry).count() == 0


def test_record_activity_failure_is_not_silenced(session):
    with pytest.raises(ValueError, match="JSON-safe"):
        record_activity(
            session,
            action="customer_updated",
            target_type="customer",
            target_id=1,
            actor_id=uuid.uuid4(),
            reason="Change Customer membership.",
            details={"after": {"unsupported": object()}},
        )

    assert session.query(AuditEntry).count() == 0


def _entry(
    session,
    *,
    action: str,
    target_type: str,
    target_id: object,
    actor: str,
    created_at: datetime,
) -> AuditEntry:
    entry = record_activity(
        session,
        action=action,
        target_type=target_type,
        target_id=target_id,
        actor_id=actor,
        reason=f"Reason for {action}",
        details={
            "before": {"status": "before"},
            "after": {"status": "after"},
        },
    )
    entry.created_at = created_at
    session.flush()
    return entry


def test_one_query_filters_combines_paginates_and_backs_entity_timelines(
    session,
):
    base = datetime(2026, 7, 20, 12, tzinfo=timezone.utc)
    customer_id = 42
    newest = _entry(
        session,
        action="customer_updated",
        target_type="customer",
        target_id=customer_id,
        actor="admin:one",
        created_at=base + timedelta(days=3),
    )
    _entry(
        session,
        action="customer_deactivated",
        target_type="customer",
        target_id=customer_id,
        actor="admin:two",
        created_at=base + timedelta(days=2),
    )
    _entry(
        session,
        action="agency_updated",
        target_type="agency",
        target_id=7,
        actor="admin:one",
        created_at=base + timedelta(days=1),
    )
    _entry(
        session,
        action="scraper_configuration_scheduled",
        target_type="scraper_configuration",
        # Deterministic and deliberately excludes the Customer id text below;
        # a random UUID containing "42" made the free-text assertion flaky.
        target_id=uuid.UUID("11111111-1111-4111-8111-111111111111"),
        actor="admin:three",
        created_at=base,
    )
    session.commit()

    assert query_activity(session, actor="admin:two")["total"] == 1
    assert query_activity(session, action="agency_updated")["total"] == 1
    assert query_activity(session, entity_type="customer")["total"] == 2
    assert query_activity(session, query="DEACTIVATED")["total"] == 1
    assert query_activity(session, query="admin:three")["total"] == 1
    assert query_activity(session, query=str(customer_id))["total"] == 2
    assert query_activity(
        session,
        date_from=date(2026, 7, 22),
        date_to=date(2026, 7, 23),
    )["total"] == 2
    combined = query_activity(
        session,
        actor="admin:one",
        entity_type="customer",
        entity_id=str(customer_id),
        date_from=date(2026, 7, 23),
        date_to=date(2026, 7, 23),
    )
    assert combined["total"] == 1
    assert combined["entries"][0]["id"] == str(newest.id)

    first = query_activity(session, page=1, page_size=2)
    second = query_activity(session, page=2, page_size=2)
    assert first["total"] == 4
    assert first["pages"] == 2
    assert [entry["action"] for entry in first["entries"]] == [
        "customer_updated",
        "customer_deactivated",
    ]
    assert [entry["action"] for entry in second["entries"]] == [
        "agency_updated",
        "scraper_configuration_scheduled",
    ]

    timeline = query_activity(
        session,
        entity_type="customer",
        entity_id=str(customer_id),
    )
    global_match = query_activity(
        session,
        action="customer_updated",
        entity_type="customer",
        entity_id=str(customer_id),
    )
    assert global_match["entries"][0] == timeline["entries"][0]
    assert timeline["entries"][0]["entityHref"] == "/app/admin/customers/42"


def test_activity_endpoints_are_admin_only_and_share_the_query(
    session,
    settings,
):
    entry = _entry(
        session,
        action="customer_updated",
        target_type="customer",
        target_id=42,
        actor="admin:one",
        created_at=datetime(2026, 7, 23, 12, tzinfo=timezone.utc),
    )
    session.commit()

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=uuid.uuid4(),
        email="admin@example.com",
        role="admin",
        audience="admin",
        csrf="test",
    )
    try:
        client = TestClient(app)
        global_view = client.get(
            "/api/admin/activity",
            params={
                "actor": "admin:one",
                "action": "customer_updated",
                "entityType": "customer",
                "entityId": "42",
                "dateFrom": "2026-07-23",
                "dateTo": "2026-07-23",
                "q": "Reason for customer_updated",
            },
        )
        timeline = client.get(
            "/api/admin/activity",
            params={"entityType": "customer", "entityId": "42"},
        )
        assert global_view.status_code == 200
        assert timeline.status_code == 200
        assert global_view.json()["entries"][0]["id"] == str(entry.id)
        assert global_view.json()["entries"][0] == timeline.json()["entries"][0]
        assert "filters" in global_view.json()
        refused = client.get("/api/admin/activity", params={"refused": "true"})
        assert refused.json()["total"] == 0
        operator = client.get("/api/admin/activity", params={"who": "operator"})
        assert operator.json()["total"] == 1
    finally:
        app.dependency_overrides.clear()

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[require_principal] = lambda: Principal(
        user_id=uuid.uuid4(),
        email="customer@example.com",
        role="customer",
        audience="customer",
        csrf="test",
    )
    try:
        client = TestClient(app)
        assert client.get("/api/admin/activity").status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_activity_endpoints_refuse_unauthenticated_sessions(
    session,
    settings,
):
    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        client = TestClient(app)
        assert client.get("/api/admin/activity").status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_entries_name_their_actor_action_and_record(session):
    from jawnix.models import Agent, CustomerProfile, LeadRequest, UserAccount

    customer = Agent(slug="northstar", name="Northstar Insurance")
    session.add(customer)
    session.flush()
    river = uuid.uuid4()
    session.add_all(
        [
            CustomerProfile(
                user_id=river,
                email="river@northstar.example",
                first_name="River",
                last_name="Morgan",
                agent_id=customer.id,
            ),
            UserAccount(
                auth_user_id=river,
                customer_id=customer.id,
                email="river@northstar.example",
            ),
        ]
    )
    request = LeadRequest(
        user_id=river,
        agent=customer,
        lead_count=25_000,
        states_snapshot=["FL"],
        state_mode="all_saved",
        delivery_email="river@northstar.example",
        status="approved",
    )
    session.add(request)
    session.flush()
    base = datetime(2026, 11, 9, 9, tzinfo=timezone.utc)
    _entry(
        session,
        action="batch_request_approve",
        target_type="batch_request",
        target_id=request.id,
        actor="telegram:12345",
        created_at=base,
    )
    _entry(
        session,
        action="customer_hard_delete_refused",
        target_type="customer",
        target_id=customer.id,
        actor=str(uuid.uuid4()),
        created_at=base - timedelta(hours=1),
    )
    _entry(
        session,
        action="licensed_states_update",
        target_type="customer",
        target_id=customer.id,
        actor=str(river),
        created_at=base - timedelta(hours=2),
    )
    _entry(
        session,
        action="source_niche_confirmed",
        target_type="source_segment",
        target_id="TX::roofing contractor",
        actor="system:nightly-scheduler",
        created_at=base - timedelta(hours=3),
    )
    session.commit()

    entries = query_activity(session)["entries"]

    approve, refused, states, niche = entries
    assert approve["actionLabel"] == "Batch approved"
    assert approve["actorName"] == "Operator"
    assert approve["entityLabel"] == f"Batch #{request.number}"
    assert approve["entityHref"] == f"/app/admin/batches/{request.number}"
    assert approve["refused"] is False
    assert refused["actionLabel"] == "Refused: permanent deletion"
    assert refused["refused"] is True
    assert refused["entityLabel"] == "Northstar Insurance"
    assert states["actorName"] == "River Morgan"
    assert states["actionLabel"] == "Appointed states changed"
    assert niche["actorName"] == "System"
    assert niche["entityLabel"] == "roofing contractor · TX"
    assert niche["entityTypeLabel"] == "Niche mapping"
    assert niche["entityHref"] == (
        "/app/admin/setup/niche-mappings?segment=TX%3A%3Aroofing%20contractor"
    )

    assert [
        entry["action"] for entry in query_activity(session, refused=True)["entries"]
    ] == ["customer_hard_delete_refused"]
    assert query_activity(session, who="system")["total"] == 1
    assert query_activity(session, who="operator")["total"] == 2
    assert query_activity(session, who=str(river))["total"] == 1


def test_filters_offer_every_recorded_type_and_everyone_who_acted(session):
    from jawnix.activity import activity_filters
    from jawnix.models import CustomerRegistration

    casey = uuid.uuid4()
    session.add(
        CustomerRegistration(
            auth_user_id=casey,
            email="casey@example.com",
            first_name="Casey",
            last_name="Reyes",
            phone="2145550100",
            licensed_states=["TX"],
            status="awaiting_review",
        )
    )
    _entry(
        session,
        action="customer_registration_review_requested",
        target_type="customer_registration_v2",
        target_id=casey,
        actor=f"user_account:{casey}",
        created_at=datetime(2026, 11, 9, tzinfo=timezone.utc),
    )
    session.commit()

    filters = activity_filters(session)

    types = {item["value"]: item["label"] for item in filters["entityTypes"]}
    assert types["customer_registration_v2"] == "Customer registration v2"
    assert types["batch_request"] == "Batch"
    assert types["scrape_run"] == "Scrape run"
    who = filters["who"]
    assert who[:2] == [
        {"value": "operator", "label": "Operator"},
        {"value": "system", "label": "System"},
    ]
    assert {"value": f"user_account:{casey}", "label": "Casey Reyes"} in who
