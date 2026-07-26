from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from jawnix.allocation import allocate_request
from jawnix.api import app
from jawnix.auth import Principal, require_admin, require_principal
from jawnix.config import get_settings
from jawnix.database import get_db
from jawnix.models import (
    Agency,
    AuditEntry,
    Agent,
    BatchArtifact,
    CustomerProfile,
    CustomerTombstone,
    DistributionEvent,
    Job,
    Lead,
    LeadOutcome,
    LeadCorrectionEvent,
    LeadReport,
    LeadRequest,
    ScraperConfiguration,
    ScrapeAnomaly,
    ScraperRun,
    SourceSegment,
    SourceRecommendation,
    UserAccount,
    utcnow,
)
from jawnix.telegram import anomaly_callback_data, callback_data
from jawnix.performance import build_source_recommendations


def test_request_mapping_state_validation_cancel_and_billing_404(session):
    user_id = uuid.uuid4()
    agent = Agent(slug="api-agent", name="API Agent")
    profile = CustomerProfile(
        user_id=user_id,
        email="customer@example.com",
        licensed_states=["TX", "FL"],
    )
    session.add_all([agent, profile])
    session.commit()

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[require_principal] = lambda: Principal(
        user_id=user_id,
        email=profile.email,
        role="customer",
        csrf="test",
    )
    try:
        client = TestClient(app)
        assert client.post("/api/generate-invoice", json={}).status_code == 404
        assert client.post(
            "/api/me/requests",
            json={"lead_count": 10, "state_mode": "all_saved", "states": []},
        ).status_code == 409

        profile.agent_id = agent.id
        profile.mapping_confirmed_at = utcnow()
        session.commit()
        session.expire(profile, ["agent"])
        outside = client.post(
            "/api/me/requests",
            json={"lead_count": 10, "state_mode": "selected", "states": ["PA"]},
        )
        assert outside.status_code == 422

        created = client.post(
            "/api/me/requests",
            json={"lead_count": 10, "state_mode": "selected", "states": ["TX"]},
        )
        assert created.status_code == 201
        request_id = created.json()["id"]
        assert created.json()["states_snapshot"] == ["TX"]
        assert session.scalar(select(func.count(Job.id)).where(Job.kind == "notify_request")) == 1
        assert client.delete(f"/api/me/requests/{request_id}").status_code == 200
        assert session.get(LeadRequest, uuid.UUID(request_id)).status == "canceled"
        assert client.delete(f"/api/me/requests/{request_id}").status_code == 409

        approved = client.post(
            "/api/me/requests",
            json={"lead_count": 10, "state_mode": "selected", "states": ["TX"]},
        )
        approved_id = uuid.UUID(approved.json()["id"])
        session.get(LeadRequest, approved_id).status = "approved"
        session.commit()
        assert client.delete(f"/api/me/requests/{approved_id}").status_code == 200

        agent.active = False
        session.commit()
        assert client.post(
            "/api/me/requests",
            json={"lead_count": 10, "state_mode": "selected", "states": ["TX"]},
        ).status_code == 409
    finally:
        app.dependency_overrides.clear()


def test_customer_state_removal_narrows_unallocated_requests_without_expanding_them(session):
    user_id = uuid.uuid4()
    customer = Agent(slug="licensed-customer", name="Licensed Customer")
    profile = CustomerProfile(
        user_id=user_id,
        email="licensed@example.com",
        first_name="Licensed",
        last_name="Customer",
        licensed_states=["TX", "FL"],
        agent=customer,
        mapping_confirmed_at=utcnow(),
    )
    narrowed = LeadRequest(
        user_id=user_id,
        agent=customer,
        lead_count=10,
        state_mode="all_saved",
        states_snapshot=["TX", "FL"],
        delivery_email=profile.email,
        status="approved",
        approved_at=utcnow(),
    )
    canceled = LeadRequest(
        user_id=user_id,
        agent=customer,
        lead_count=5,
        state_mode="selected",
        states_snapshot=["TX"],
        delivery_email=profile.email,
        status="waiting_inventory",
        approved_at=utcnow(),
    )
    committed = LeadRequest(
        user_id=user_id,
        agent=customer,
        lead_count=1,
        state_mode="all_saved",
        states_snapshot=["TX", "FL"],
        delivery_email=profile.email,
        status="generated",
        approved_at=utcnow(),
        processed_at=utcnow(),
    )
    session.add_all([customer, profile, narrowed, canceled, committed])
    session.commit()

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[require_principal] = lambda: Principal(
        user_id=user_id,
        email=profile.email,
        role="customer",
        csrf="test",
    )
    try:
        response = TestClient(app).patch(
            "/api/me/profile",
            json={
                "first_name": profile.first_name,
                "last_name": profile.last_name,
                "phone": "",
                "licensed_states": ["FL", "CA"],
            },
        )
        assert response.status_code == 200

        session.refresh(narrowed)
        session.refresh(canceled)
        session.refresh(committed)
        assert narrowed.states_snapshot == ["FL"]
        assert narrowed.status == "approved"
        assert narrowed.approved_at is not None
        assert "TX" in narrowed.status_message
        assert canceled.states_snapshot == []
        assert canceled.status == "canceled"
        assert committed.states_snapshot == ["TX", "FL"]

        updates = list(
            session.scalars(
                select(Job)
                .where(Job.kind == "licensed_states_changed")
                .order_by(Job.id)
            )
        )
        assert len(updates) == 2
        assert updates[0].request_id == narrowed.id
        assert updates[0].payload == {
            "added": ["CA"],
            "removed": ["TX"],
            "requestAction": "narrowed",
            "states": ["FL"],
        }
        assert updates[1].request_id == canceled.id
        assert updates[1].payload["requestAction"] == "canceled"
    finally:
        app.dependency_overrides.clear()


def test_late_feedback_keeps_correction_history_and_performance_denominators(
    session,
):
    user_id = uuid.uuid4()
    customer = Agent(slug="feedback-customer", name="Feedback Customer")
    profile = CustomerProfile(
        user_id=user_id,
        email="feedback@example.com",
        licensed_states=["TX"],
        agent=customer,
        mapping_confirmed_at=utcnow(),
    )
    lead = Lead(phone="2145555001", title="Roofing One", state="TX")
    legacy = Lead(
        phone="2145555002",
        title="Legacy",
        state="TX",
        source_flow="nppes",
    )
    session.add_all([customer, profile, lead, legacy])
    session.flush()
    delivered = DistributionEvent(
        lead_id=lead.id,
        agent_id=customer.id,
        customer_name=customer.name,
        phone=lead.phone,
        title=lead.title,
        state=lead.state,
        source_kind="google_maps",
        source_segment_key="roofing|TX|maps",
        source_niche="roofing",
        delivered_at=utcnow(),
    )
    legacy_delivered = DistributionEvent(
        lead_id=legacy.id,
        agent_id=customer.id,
        customer_name=customer.name,
        phone=legacy.phone,
        title=legacy.title,
        state=legacy.state,
        source_kind="legacy",
        delivered_at=utcnow(),
    )
    session.add_all([delivered, legacy_delivered])
    session.commit()

    def database_override():
        yield session

    principal = Principal(
        user_id=user_id,
        email=profile.email,
        role="customer",
        csrf="test",
    )
    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[require_principal] = lambda: principal
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=uuid.uuid4(),
        email="admin@example.com",
        role="admin",
        csrf="test",
    )
    try:
        client = TestClient(app)
        poor = client.post(
            f"/api/me/distributions/{delivered.id}/outcomes",
            json={"kind": "poor"},
        )
        assert poor.status_code == 201
        positive = client.post(
            f"/api/me/distributions/{delivered.id}/outcomes",
            json={"kind": "positive_response"},
        )
        assert positive.status_code == 201
        assert client.post(
            f"/api/me/distributions/{delivered.id}/outcomes",
            json={"kind": "positive_response"},
        ).status_code == 409
        assert client.post(
            f"/api/me/distributions/{delivered.id}/outcomes",
            json={"kind": "appointment_booked"},
        ).status_code == 422

        corrected = client.post(
            f"/api/me/distributions/{delivered.id}/outcomes",
            json={
                "kind": "good",
                "supersedes_outcome_id": poor.json()["id"],
            },
        )
        assert corrected.status_code == 201
        history = client.get(
            f"/api/me/distributions/{delivered.id}/outcomes"
        )
        assert history.status_code == 200
        assert [item["kind"] for item in history.json()] == [
            "poor",
            "positive_response",
            "good",
        ]

        performance = client.get("/api/admin/source-performance")
        assert performance.status_code == 200
        body = performance.json()
        assert body["cohorts"] == body["segments"]
        assert body["cohorts"] == [
            {
                "segment": "roofing|TX|maps",
                "niche": "roofing",
                "distributionPeriod": delivered.delivered_at.strftime(
                    "%Y-%m"
                ),
                "delivered": 1,
                "rated": 1,
                "good": 1,
                "poor": 0,
                "positiveResponses": 1,
                "appointmentsBooked": 0,
                "goodRate": 1.0,
                "positiveResponseRate": 1.0,
                "appointmentRate": 0.0,
                "qualityStatus": "insufficient_data",
                "conversionStatus": "insufficient_data",
            }
        ]
        assert body["global"]["prescriptive"] is False
        assert body["legacy"] == {
            "delivered": 1,
            "excludedFromRecommendations": True,
        }
        assert session.scalar(select(func.count(LeadOutcome.id))) == 3
    finally:
        app.dependency_overrides.clear()


def test_admin_can_send_recipient_password_reset(session, settings, monkeypatch):
    user_id = uuid.uuid4()
    profile = CustomerProfile(
        user_id=user_id,
        email="activation@example.com",
        licensed_states=[],
    )
    session.add(profile)
    session.commit()
    sent: list[str] = []

    async def fake_send(_settings, email):
        sent.append(email)

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=uuid.uuid4(),
        email="admin@example.com",
        role="admin",
        csrf="test",
    )
    monkeypatch.setattr("jawnix.api._send_password_reset", fake_send)
    try:
        client = TestClient(app)
        response = client.post(f"/api/admin/recipients/{user_id}/send-password-reset")
        assert response.status_code == 200
        assert response.json() == {"ok": True, "email": profile.email}
        assert sent == [profile.email]
        assert client.post(f"/api/admin/recipients/{uuid.uuid4()}/send-password-reset").status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_admin_can_view_and_edit_agency_agent_hierarchy(session):
    first_agency = Agency(slug="first-agency", name="First Agency")
    second_agency = Agency(slug="second-agency", name="Second Agency", active=False)
    agent = Agent(slug="hierarchy-agent", name="Hierarchy Agent", agency=first_agency, active=False)
    cascade_agent = Agent(slug="cascade-agent", name="Cascade Agent", agency=first_agency)
    profile = CustomerProfile(
        user_id=uuid.uuid4(),
        email="hierarchy@example.com",
        licensed_states=["TX"],
        agent=agent,
        mapping_confirmed_at=utcnow(),
    )
    cascade_profile = CustomerProfile(
        user_id=uuid.uuid4(),
        email="cascade@example.com",
        licensed_states=["FL"],
        agent=cascade_agent,
        mapping_confirmed_at=utcnow(),
    )
    lead = Lead(phone="2125550100", title="Test", state="NY")
    session.add_all([first_agency, second_agency, agent, cascade_agent, profile, cascade_profile, lead])
    session.flush()
    event = DistributionEvent(lead_id=lead.id, agent_id=agent.id, source="test")
    session.add(event)
    session.commit()

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=uuid.uuid4(),
        email="admin@example.com",
        role="admin",
        csrf="test",
    )
    try:
        client = TestClient(app)
        hierarchy = client.get("/api/admin/recipients")
        assert hierarchy.status_code == 200
        data = hierarchy.json()
        assert {item["slug"] for item in data["agencies"]} == {"first-agency", "second-agency"}
        returned_agent = next(item for item in data["agents"] if item["slug"] == agent.slug)
        assert returned_agent == {
            "id": agent.id,
            "slug": "hierarchy-agent",
            "name": "Hierarchy Agent",
            "active": False,
            "agencyId": first_agency.id,
            "agency": "First Agency",
        }
        customers = client.get("/api/admin/customers")
        assert customers.status_code == 200
        returned_customer = next(
            item
            for item in customers.json()["customers"]
            if item["slug"] == agent.slug
        )
        assert returned_customer["id"] == agent.id
        assert returned_customer["name"] == "Hierarchy Agent"
        assert "agents" not in customers.json()

        agency_update = client.patch(
            f"/api/admin/agencies/{second_agency.id}",
            json={"name": "Renamed Agency", "active": True},
        )
        assert agency_update.status_code == 200
        session.refresh(second_agency)
        assert second_agency.name == "Renamed Agency"
        assert second_agency.active is True

        agent_update = client.patch(
            f"/api/admin/agents/{agent.id}",
            json={"name": "Renamed Agent", "agency_id": second_agency.id, "active": True},
        )
        assert agent_update.status_code == 200
        session.refresh(agent)
        assert agent.name == "Renamed Agent"
        assert agent.agency_id == second_agency.id
        assert agent.active is True

        assert client.patch(
            f"/api/admin/agents/{agent.id}",
            json={"name": "Agent", "agency_id": 999_999, "active": True},
        ).status_code == 404
        assert client.patch(
            f"/api/admin/agencies/{first_agency.id}",
            json={"name": " ", "active": True},
        ).status_code == 422
        assert client.patch(
            f"/api/admin/agencies/{first_agency.id}",
            json={"name": "Agency", "active": True, "slug": "cannot-change"},
        ).status_code == 422

        assert client.request(
            "DELETE",
            f"/api/admin/agents/{agent.id}",
            json={"confirm_slug": "wrong"},
        ).status_code == 409
        deleted_agent = client.request(
            "DELETE",
            f"/api/admin/agents/{agent.id}",
            json={"confirm_slug": agent.slug},
        )
        assert deleted_agent.status_code == 200
        assert deleted_agent.json() == {
            "ok": True,
            "unassignedRecipients": 1,
            "historyPreserved": True,
        }
        session.refresh(agent)
        session.refresh(profile)
        session.refresh(event)
        assert agent.active is False
        assert agent.deleted_at is not None
        assert profile.agent_id is None
        assert profile.mapping_confirmed_at is None
        assert event.agent_id == agent.id

        active_request = LeadRequest(
            user_id=cascade_profile.user_id,
            agent_id=cascade_agent.id,
            lead_count=10,
            state_mode="all_saved",
            states_snapshot=["FL"],
            delivery_email=cascade_profile.email,
            status="pending",
        )
        first_agency.active = False
        session.add(active_request)
        session.commit()
        active_customer_delete = client.request(
            "DELETE",
            f"/api/admin/agencies/{first_agency.id}",
            json={"confirm_slug": first_agency.slug},
        )
        assert active_customer_delete.status_code == 409
        assert active_customer_delete.json()["detail"] == {
            "message": (
                "Deactivate every Customer before deleting the Agency."
            ),
            "activeCustomers": [cascade_agent.slug],
        }
        cascade_agent.active = False
        session.commit()
        blocked_agency_delete = client.request(
            "DELETE",
            f"/api/admin/agencies/{first_agency.id}",
            json={"confirm_slug": first_agency.slug},
        )
        assert blocked_agency_delete.status_code == 409
        assert blocked_agency_delete.json()["detail"] == "Resolve 1 active request(s) before deleting this agency."
        active_request.status = "canceled"
        session.commit()

        deleted_agency = client.request(
            "DELETE",
            f"/api/admin/agencies/{first_agency.id}",
            json={"confirm_slug": first_agency.slug},
        )
        assert deleted_agency.status_code == 200
        assert deleted_agency.json() == {
            "ok": True,
            "deletedCustomers": 1,
            "unassignedRecipients": 1,
            "historyPreserved": True,
        }
        session.refresh(first_agency)
        session.refresh(cascade_agent)
        session.refresh(cascade_profile)
        assert first_agency.deleted_at is not None
        assert cascade_agent.deleted_at is not None
        assert cascade_profile.agent_id is None
        assert cascade_profile.mapping_confirmed_at is None

        remaining = client.get("/api/admin/recipients").json()
        assert "first-agency" not in {item["slug"] for item in remaining["agencies"]}
        assert "hierarchy-agent" not in {item["slug"] for item in remaining["agents"]}
        assert "cascade-agent" not in {item["slug"] for item in remaining["agents"]}
    finally:
        app.dependency_overrides.clear()


def test_admin_replaces_user_account_without_replacing_customer_identity(
    session,
):
    old_user_id = uuid.uuid4()
    new_user_id = uuid.uuid4()
    customer = Agent(
        slug="replaceable-account-customer",
        name="Replaceable Account Customer",
        licensed_states=["TX", "FL"],
    )
    old_profile = CustomerProfile(
        user_id=old_user_id,
        email="old-account@example.com",
        licensed_states=["TX", "FL"],
        agent=customer,
        mapping_confirmed_at=utcnow(),
    )
    old_account = UserAccount(
        auth_user_id=old_user_id,
        email=old_profile.email,
        customer=customer,
        active=True,
    )
    session.add_all([customer, old_profile, old_account])
    session.commit()

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=uuid.uuid4(),
        email="admin@example.com",
        role="admin",
        csrf="test",
    )
    try:
        client = TestClient(app)
        replaced = client.put(
            f"/api/admin/customers/{customer.id}/user-account",
            json={
                "auth_user_id": str(new_user_id),
                "email": "new-account@example.com",
            },
        )
        assert replaced.status_code == 200
        assert replaced.json() == {
            "customerId": customer.id,
            "authUserId": str(new_user_id),
            "email": "new-account@example.com",
            "licensedStates": ["FL", "TX"],
        }

        session.refresh(old_account)
        assert old_account.active is False
        assert old_account.replaced_at is not None
        new_account = session.scalar(
            select(UserAccount).where(
                UserAccount.auth_user_id == new_user_id
            )
        )
        assert new_account is not None
        assert new_account.customer_id == customer.id
        assert new_account.active is True
        new_profile = session.get(CustomerProfile, new_user_id)
        assert new_profile is not None
        assert new_profile.customer_id == customer.id
        assert new_profile.licensed_states == ["FL", "TX"]

        app.dependency_overrides[require_principal] = lambda: Principal(
            user_id=old_user_id,
            email=old_profile.email,
            role="customer",
            csrf="test",
        )
        assert client.get("/api/me/profile").status_code == 403

        app.dependency_overrides[require_principal] = lambda: Principal(
            user_id=new_user_id,
            email=new_profile.email,
            role="customer",
            csrf="test",
        )
        current = client.get("/api/me/profile")
        assert current.status_code == 200
        assert current.json()["customer_id"] == customer.id
        assert current.json()["licensed_states"] == ["FL", "TX"]
    finally:
        app.dependency_overrides.clear()


def test_customer_lifecycle_blocks_hard_delete_and_erases_to_tombstone(
    session,
):
    admin_id = uuid.uuid4()
    user_id = uuid.uuid4()
    customer = Agent(slug="history-customer", name="History Customer")
    profile = CustomerProfile(
        user_id=user_id,
        email="private@example.com",
        first_name="Private",
        last_name="Person",
        phone="2145550000",
        licensed_states=["TX"],
        agent=customer,
        mapping_confirmed_at=utcnow(),
    )
    account = UserAccount(
        auth_user_id=user_id,
        customer=customer,
        email=profile.email,
    )
    lead = Lead(phone="2145556400", title="History", state="TX")
    session.add_all([customer, profile, account, lead])
    session.flush()
    event = DistributionEvent(
        lead_id=lead.id,
        agent_id=customer.id,
        customer_name=customer.name,
        phone=lead.phone,
        title=lead.title,
        state=lead.state,
        source="lifecycle-test",
    )
    empty = Agent(
        slug="empty-customer",
        name="Empty Customer",
        active=False,
    )
    session.add_all([event, empty])
    session.commit()

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=admin_id,
        email="admin@example.com",
        role="admin",
        csrf="test",
    )
    try:
        client = TestClient(app)
        deactivated = client.patch(
            f"/api/admin/customers/{customer.id}",
            json={
                "name": customer.name,
                "agency_id": None,
                "active": False,
                "reason": "Customer ended service.",
            },
        )
        assert deactivated.status_code == 200
        blocked = client.request(
            "DELETE",
            f"/api/admin/customers/{customer.id}",
            json={
                "confirm_slug": customer.slug,
                "hard_delete": True,
                "reason": "Requested deletion.",
            },
        )
        assert blocked.status_code == 409
        assert blocked.json()["detail"]["dependencies"]["distributions"] == 1

        erased = client.post(
            f"/api/admin/customers/{customer.id}/erase",
            json={"reason": "Verified personal-data erasure request."},
        )
        assert erased.status_code == 200
        session.refresh(customer)
        session.refresh(profile)
        session.refresh(account)
        session.refresh(event)
        assert customer.name == "Deleted Customer"
        assert profile.first_name == profile.last_name == profile.phone == ""
        assert profile.email.endswith("@invalid.local")
        assert account.active is False
        assert event.agent_id == customer.id
        assert session.query(CustomerTombstone).filter_by(
            former_customer_id=customer.id
        ).count() == 1

        deleted = client.request(
            "DELETE",
            f"/api/admin/customers/{empty.id}",
            json={
                "confirm_slug": empty.slug,
                "hard_delete": True,
                "reason": "Unused duplicate Customer.",
            },
        )
        assert deleted.status_code == 200
        assert deleted.json()["hardDeleted"] is True
        assert session.get(Agent, empty.id) is None
    finally:
        app.dependency_overrides.clear()


def test_admin_creates_immutable_versioned_scraper_configuration(session):
    admin_id = uuid.uuid4()

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=admin_id,
        email="admin@example.com",
        role="admin",
        csrf="test",
    )
    try:
        client = TestClient(app)
        created = client.post(
            "/api/admin/scraper-configurations",
            json={
                "reason": "Add Texas roofing acquisition",
                "segments": [
                    {
                        "key": "roofing-austin-tx",
                        "niche": "Roofing",
                        "query": "roofing contractor",
                        "geography": "Austin, TX",
                        "parameters": {"pages": 4},
                    }
                ],
                "anomaly_thresholds": {
                    "down_fraction": 0.5,
                    "up_multiplier": 2.0,
                    "history_runs": 7,
                },
            },
        )
        assert created.status_code == 201
        body = created.json()
        assert body["version"] == 1
        assert body["status"] == "draft"
        assert body["createdBy"] == str(admin_id)
        assert body["checksum"]
        assert body["segments"] == [
            {
                "key": "roofing-austin-tx",
                "niche": "Roofing",
                "query": "roofing contractor",
                "geography": "Austin, TX",
                "parameters": {"pages": 4},
            }
        ]

        second = client.post(
            "/api/admin/scraper-configurations",
            json={
                "reason": "Reduce acquisition pages",
                "segments": [
                    {
                        "key": "roofing-austin-tx",
                        "niche": "Roofing",
                        "query": "roofing contractor",
                        "geography": "Austin, TX",
                        "parameters": {"pages": 2},
                    }
                ],
            },
        )
        assert second.status_code == 201
        assert second.json()["version"] == 2
        assert client.patch(
            f"/api/admin/scraper-configurations/{body['id']}",
            json={"status": "active"},
        ).status_code == 405

        configurations = client.get(
            "/api/admin/scraper-configurations"
        )
        assert configurations.status_code == 200
        assert [
            item["version"] for item in configurations.json()
        ] == [2, 1]
        assert session.query(ScraperConfiguration).count() == 2
        assert session.query(SourceSegment).count() == 2
    finally:
        app.dependency_overrides.clear()


def test_configuration_schedule_manual_run_and_rollback_are_separate_audited_actions(
    session,
):
    admin_id = uuid.uuid4()

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=admin_id,
        email="admin@example.com",
        role="admin",
        csrf="test",
    )
    try:
        client = TestClient(app)
        payload = {
            "reason": "Initial configuration",
            "segments": [
                {
                    "key": "dentists-miami-fl",
                    "niche": "Dentists",
                    "query": "dentist",
                    "geography": "Miami, FL",
                    "parameters": {},
                }
            ],
        }
        first = client.post(
            "/api/admin/scraper-configurations",
            json=payload,
        ).json()
        second = client.post(
            "/api/admin/scraper-configurations",
            json={
                **payload,
                "reason": "Second configuration",
                "segments": [
                    {
                        **payload["segments"][0],
                        "parameters": {"pages": 2},
                    }
                ],
            },
        ).json()

        scheduled = client.post(
            f"/api/admin/scraper-configurations/{first['id']}/schedule",
            json={"reason": "Use on the next nightly run"},
        )
        assert scheduled.status_code == 200
        assert scheduled.json()["status"] == "scheduled"
        assert session.query(Job).filter_by(kind="run_scraper").count() == 0

        manual = client.post(
            f"/api/admin/scraper-configurations/{second['id']}/manual-run",
            json={"reason": "Validate it immediately"},
        )
        assert manual.status_code == 202
        assert manual.json()["configurationId"] == second["id"]
        run_job = session.query(Job).filter_by(kind="run_scraper").one()
        assert run_job.payload == {
            "configuration_id": second["id"],
            "reason": "Validate it immediately",
            "actor_user_id": str(admin_id),
            "manual": True,
        }
        session.refresh(session.get(ScraperConfiguration, uuid.UUID(second["id"])))
        assert session.get(
            ScraperConfiguration,
            uuid.UUID(second["id"]),
        ).status == "draft"

        rollback = client.post(
            f"/api/admin/scraper-configurations/{first['id']}/rollback",
            json={"reason": "Return to the known configuration"},
        )
        assert rollback.status_code == 201
        assert rollback.json()["version"] == 3
        assert rollback.json()["status"] == "scheduled"
        assert rollback.json()["basedOnConfigurationId"] == first["id"]
        assert session.query(AuditEntry).count() == 3
        assert [
            entry.action
            for entry in session.query(AuditEntry).order_by(AuditEntry.created_at)
        ] == [
            "scraper_configuration_scheduled",
            "scraper_manual_run_queued",
            "scraper_configuration_rollback_scheduled",
        ]
    finally:
        app.dependency_overrides.clear()


def test_admin_suppression_is_reversible_audited_and_controls_eligibility(
    session,
    settings,
):
    admin_id = uuid.uuid4()
    customer = Agent(slug="suppression-customer", name="Suppression Customer")
    lead = Lead(phone="2145556100", title="Suppression Lead", state="TX")
    session.add_all([customer, lead])
    session.flush()
    from conftest import make_request

    request = make_request(session, customer, 1)
    session.commit()

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=admin_id,
        email="admin@example.com",
        role="admin",
        csrf="test",
    )
    try:
        client = TestClient(app)
        suppressed = client.put(
            f"/api/admin/leads/{lead.id}/suppression",
            json={"reason": "Confirmed do-not-contact request"},
        )
        assert suppressed.status_code == 200
        assert suppressed.json()["suppressed"] is True
        result = allocate_request(session, request.id, settings)
        session.commit()
        assert result.status == "waiting_inventory"
        assert result.allocated == 0

        unsuppressed = client.request(
            "DELETE",
            f"/api/admin/leads/{lead.id}/suppression",
            json={"reason": "Restriction was entered in error"},
        )
        assert unsuppressed.status_code == 200
        assert unsuppressed.json()["suppressed"] is False
        request.status = "approved"
        result = allocate_request(session, request.id, settings)
        session.commit()
        assert result.allocated == 1
        audits = session.query(AuditEntry).filter(
            AuditEntry.target_type == "lead"
        ).order_by(AuditEntry.created_at).all()
        assert [entry.action for entry in audits] == [
            "lead_suppressed",
            "lead_unsuppressed",
        ]
        assert all(entry.reason for entry in audits)
    finally:
        app.dependency_overrides.clear()


def test_lead_correction_overrides_delivery_until_audited_removal(
    session,
    settings,
):
    admin_id = uuid.uuid4()
    customer = Agent(slug="correction-customer", name="Correction Customer")
    lead = Lead(
        phone="2145556200",
        title="Source Title",
        state="TX",
        legacy_title="Source Title",
        legacy_state="TX",
    )
    session.add_all([customer, lead])
    session.flush()

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=admin_id,
        email="admin@example.com",
        role="admin",
        csrf="test",
    )
    try:
        client = TestClient(app)
        applied = client.put(
            f"/api/admin/leads/{lead.id}/correction",
            json={
                "title": "Corrected Title",
                "state": "FL",
                "reason": "Verified with the business",
            },
        )
        assert applied.status_code == 200
        correction_id = applied.json()["correctionId"]
        session.refresh(lead)
        assert lead.title == "Corrected Title"
        assert lead.state == "FL"
        assert str(lead.active_correction_id) == correction_id

        from conftest import make_request

        request = make_request(session, customer, 1, ["FL"])
        result = allocate_request(session, request.id, settings)
        session.commit()
        assert result.allocated == 1
        event = session.scalar(
            select(DistributionEvent).where(
                DistributionEvent.request_id == request.id
            )
        )
        assert event.title == "Corrected Title"
        assert event.state == "FL"
        assert event.listing_provenance == {
            "kind": "lead_correction",
            "correctionId": correction_id,
        }

        removed = client.request(
            "DELETE",
            f"/api/admin/leads/{lead.id}/correction",
            json={"reason": "Correction no longer applies"},
        )
        assert removed.status_code == 200
        session.refresh(lead)
        assert lead.active_correction_id is None
        assert lead.title == "Source Title"
        assert lead.state == "TX"
        assert session.query(LeadCorrectionEvent).count() == 2
        assert [
            item.action
            for item in session.query(LeadCorrectionEvent).order_by(
                LeadCorrectionEvent.created_at
            )
        ] == ["applied", "removed"]
    finally:
        app.dependency_overrides.clear()


def test_customer_report_resolution_and_exact_artifact_regeneration_are_audited(
    session,
    settings,
):
    user_id = uuid.uuid4()
    admin_id = uuid.uuid4()
    customer = Agent(slug="reported-customer", name="Reported Customer")
    profile = CustomerProfile(
        user_id=user_id,
        email="reported@example.com",
        licensed_states=["TX"],
        agent=customer,
        mapping_confirmed_at=datetime.now(timezone.utc),
    )
    lead = Lead(
        phone="2145556300",
        title="Delivered Listing",
        state="TX",
    )
    session.add_all([customer, profile, lead])
    session.flush()
    from conftest import make_request

    request = make_request(session, customer, 1, ["TX"])
    allocate_request(session, request.id, settings)
    session.commit()
    event = session.scalar(
        select(DistributionEvent).where(
            DistributionEvent.request_id == request.id
        )
    )
    artifact = session.query(BatchArtifact).filter_by(
        request_id=request.id
    ).one()
    original_bytes = __import__("pathlib").Path(artifact.path).read_bytes()

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[require_principal] = lambda: Principal(
        user_id=user_id,
        email="reported@example.com",
        role="customer",
        csrf="test",
    )
    try:
        client = TestClient(app)
        submitted = client.post(
            f"/api/me/distributions/{event.id}/reports",
            json={
                "reason": "wrong_business_or_title",
                "details": "This is a different company.",
            },
        )
        assert submitted.status_code == 201
        report_id = submitted.json()["id"]
        report = session.get(LeadReport, uuid.UUID(report_id))
        assert report.details == "This is a different company."

        app.dependency_overrides[require_admin] = lambda: Principal(
            user_id=admin_id,
            email="admin@example.com",
            role="admin",
            csrf="test",
        )
        resolved = client.post(
            f"/api/admin/lead-reports/{report_id}/resolve",
            json={
                "action": "corrected",
                "note": "Verified corrected business title.",
                "title": "Correct Business",
            },
        )
        assert resolved.status_code == 200
        session.refresh(report)
        session.refresh(lead)
        assert report.status == "corrected"
        assert report.details == "This is a different company."
        assert lead.title == "Correct Business"

        artifact.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        __import__("pathlib").Path(artifact.path).unlink()
        session.commit()
        regenerated = client.post(
            f"/api/admin/requests/{request.id}/artifact/regenerate",
            json={"reason": "Customer requested the expired batch."},
        )
        assert regenerated.status_code == 200
        assert __import__("pathlib").Path(artifact.path).read_bytes() == original_bytes
        assert regenerated.json()["sha256"] == artifact.sha256
        assert session.query(AuditEntry).filter(
            AuditEntry.action.in_(
                ["lead_report_corrected", "batch_artifact_regenerated"]
            )
        ).count() == 2
    finally:
        app.dependency_overrides.clear()


def test_same_niche_recommendation_approval_versions_configuration_without_run(
    session,
):
    admin_id = uuid.uuid4()
    customer = Agent(slug="recommendation-customer", name="Recommendation")
    configuration = ScraperConfiguration(
        version=1,
        checksum="e" * 64,
        status="active",
        anomaly_thresholds={},
        created_by=admin_id,
        reason="Baseline",
        segments=[
            SourceSegment(
                key=key,
                niche="Roofing",
                query=key,
                geography="Texas",
                parameters={},
            )
            for key in ("roofing-a", "roofing-b")
        ],
    )
    session.add_all([customer, configuration])
    session.flush()
    for segment_index, segment in enumerate(("roofing-a", "roofing-b")):
        for index in range(100):
            lead = Lead(
                phone=f"{segment_index + 2}12{index:07d}",
                title=f"{segment} {index}",
                state="TX",
            )
            session.add(lead)
            session.flush()
            event = DistributionEvent(
                lead_id=lead.id,
                agent_id=customer.id,
                customer_name=customer.name,
                phone=lead.phone,
                title=lead.title,
                state="TX",
                source_kind="google_maps",
                source_segment_key=segment,
                source_niche="Roofing",
                distribution_period="2026-06",
                delivered_at=datetime(2026, 6, 15, tzinfo=timezone.utc),
                source="recommendation-test",
            )
            session.add(event)
            session.flush()
            if index < 30:
                session.add(
                    LeadOutcome(
                        distribution_event_id=event.id,
                        customer_id=customer.id,
                        kind="good" if segment == "roofing-a" else "poor",
                        metric="quality",
                    )
                )
            if segment == "roofing-a" and index < 20:
                session.add(
                    LeadOutcome(
                        distribution_event_id=event.id,
                        customer_id=customer.id,
                        kind="positive_response",
                        metric="positive_response",
                    )
                )
    build_source_recommendations(session)
    session.commit()
    expand = session.query(SourceRecommendation).filter_by(
        action="expand"
    ).one()
    assert expand.segment_key == "roofing-a"
    assert session.query(SourceRecommendation).count() == 2

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=admin_id,
        email="admin@example.com",
        role="admin",
        csrf="test",
    )
    try:
        response = TestClient(app).post(
            f"/api/admin/source-recommendations/{expand.id}/approve",
            json={"reason": "Expand the stronger same-niche segment."},
        )
        assert response.status_code == 200
        resulting = session.get(
            ScraperConfiguration,
            uuid.UUID(response.json()["resultingConfigurationId"]),
        )
        assert resulting.version == 2
        assert resulting.status == "scheduled"
        target = next(
            item for item in resulting.segments if item.key == "roofing-a"
        )
        assert target.parameters["recommendation_action"] == "expand"
        assert target.parameters["relative_weight"] == 1.25
        assert session.query(Job).filter_by(kind="run_scraper").count() == 0
    finally:
        app.dependency_overrides.clear()


def test_admin_session_does_not_create_customer_profile(session, settings, monkeypatch):
    admin_id = uuid.uuid4()

    async def fake_verify(_token, _settings):
        return {
            "id": str(admin_id),
            "email": "noah@jawnix.com",
            "app_metadata": {"jawnix_role": "admin"},
            "user_metadata": {"first_name": "Noah", "last_name": "Bleicher"},
        }

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[get_settings] = lambda: settings
    monkeypatch.setattr("jawnix.api.verify_supabase_token", fake_verify)
    try:
        client = TestClient(app)
        response = client.post(
            "/api/auth/session",
            json={"access_token": "test-access-token-long-enough", "requested_next": "/admin.html"},
        )
        assert response.status_code == 200
        assert response.json()["role"] == "admin"
        assert response.json()["next"] == "/admin.html"
        assert session.get(CustomerProfile, admin_id) is None
    finally:
        app.dependency_overrides.clear()


def test_customer_session_cannot_target_admin_portal(session, settings, monkeypatch):
    customer_id = uuid.uuid4()

    async def fake_verify(_token, _settings):
        return {
            "id": str(customer_id),
            "email": "customer@example.com",
            "app_metadata": {"jawnix_role": "customer"},
            "user_metadata": {},
        }

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[get_settings] = lambda: settings
    monkeypatch.setattr("jawnix.api.verify_supabase_token", fake_verify)
    try:
        client = TestClient(app)
        response = client.post(
            "/api/auth/session",
            json={"access_token": "test-access-token-long-enough", "requested_next": "/admin.html"},
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "Sign in with noah@jawnix.com to access administration."
        assert session.get(CustomerProfile, customer_id) is None
    finally:
        app.dependency_overrides.clear()


def test_telegram_webhook_authorization_replay_and_async_queue(session, settings, monkeypatch):
    user_id = uuid.uuid4()
    agent = Agent(slug="telegram-agent", name="Telegram Agent")
    profile = CustomerProfile(
        user_id=user_id,
        email="telegram@example.com",
        licensed_states=["TX"],
        agent=agent,
        mapping_confirmed_at=utcnow(),
    )
    item = LeadRequest(
        user_id=user_id,
        agent=agent,
        lead_count=10,
        state_mode="all_saved",
        states_snapshot=["TX"],
        delivery_email=profile.email,
        status="pending",
    )
    session.add_all([profile, item])
    configuration = ScraperConfiguration(
        version=999,
        checksum="f" * 64,
        status="active",
        anomaly_thresholds={},
        created_by=uuid.uuid4(),
        reason="Telegram anomaly test",
    )
    session.add(configuration)
    session.flush()
    scrape_run = ScraperRun(
        source="google_maps",
        source_version="held",
        configuration_id=configuration.id,
        status="held_anomaly",
    )
    session.add(scrape_run)
    session.flush()
    anomaly = ScrapeAnomaly(
        scraper_run_id=scrape_run.id,
        configuration_id=configuration.id,
        dataset_checksum="e" * 64,
        status="pending",
    )
    session.add(anomaly)
    session.commit()

    configured = settings.model_copy(
        update={
            "telegram_bot_token": "test-bot-token",
            "telegram_chat_id": "6775236603",
            "telegram_approver_user_ids": "6775236603",
            "telegram_webhook_secret": "webhook-secret",
        }
    )
    monkeypatch.setattr("jawnix.telegram.TelegramClient.answer_callback", lambda *args, **kwargs: None)

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[get_settings] = lambda: configured
    headers = {"X-Telegram-Bot-Api-Secret-Token": "webhook-secret"}

    def update(update_id: int, approver: str = "6775236603"):
        return {
            "update_id": update_id,
            "callback_query": {
                "id": f"callback-{update_id}",
                "from": {"id": int(approver)},
                "message": {"chat": {"id": 6775236603}},
                "data": callback_data("approve", item.id),
            },
        }

    try:
        client = TestClient(app)
        assert client.post("/api/integrations/telegram/webhook", json=update(1)).status_code == 401
        queued = client.post("/api/integrations/telegram/webhook", headers=headers, json=update(1))
        assert queued.status_code == 200
        assert queued.json()["queued"] is True
        assert session.scalar(select(func.count(Job.id)).where(Job.kind == "telegram_action")) == 1

        duplicate = client.post("/api/integrations/telegram/webhook", headers=headers, json=update(1))
        assert duplicate.json()["duplicate"] is True
        assert session.scalar(select(func.count(Job.id)).where(Job.kind == "telegram_action")) == 1

        unauthorized = client.post(
            "/api/integrations/telegram/webhook",
            headers=headers,
            json=update(2, approver="123"),
        )
        assert unauthorized.json()["ignored"] is True
        assert session.scalar(select(func.count(Job.id)).where(Job.kind == "telegram_action")) == 1

        anomaly_update = update(3)
        anomaly_update["callback_query"]["data"] = anomaly_callback_data(
            "confirm",
            anomaly.id,
        )
        queued_anomaly = client.post(
            "/api/integrations/telegram/webhook",
            headers=headers,
            json=anomaly_update,
        )
        assert queued_anomaly.status_code == 200
        anomaly_job = session.scalar(
            select(Job).where(Job.kind == "telegram_anomaly_action")
        )
        assert anomaly_job is not None
        assert anomaly_job.request_id is None
        assert anomaly_job.payload["anomaly_id"] == str(anomaly.id)
        assert anomaly_job.payload["action"] == "confirm"
    finally:
        app.dependency_overrides.clear()
