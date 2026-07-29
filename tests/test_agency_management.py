from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from jawnix.allocation import allocate_request
from jawnix.api import app
from jawnix.auth import Principal, require_admin
from jawnix.config import get_settings
from jawnix.database import get_db
from jawnix.models import (
    Agency,
    AgencyMembershipHistory,
    AuditEntry,
    Customer,
    DistributionEvent,
    Lead,
)

from conftest import make_request


def _admin_client(session, settings) -> TestClient:
    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        email="admin@example.com",
        role="admin",
        csrf="test",
    )
    return TestClient(app)


def test_confirmed_assignment_permanently_merges_both_histories_without_affecting_unrelated_agencies(
    session,
    settings,
):
    origin = Agency(slug="origin", name="Origin")
    destination = Agency(slug="destination", name="Destination")
    unrelated = Agency(slug="unrelated", name="Unrelated")
    moved = Customer(slug="moved", name="Moved Customer", agency=origin)
    destination_member = Customer(
        slug="destination-member",
        name="Destination Member",
        agency=destination,
    )
    unrelated_member = Customer(
        slug="unrelated-member",
        name="Unrelated Member",
        agency=unrelated,
    )
    session.add_all(
        [
            origin,
            destination,
            unrelated,
            moved,
            destination_member,
            unrelated_member,
        ]
    )
    session.flush()

    old = datetime.now(timezone.utc) - timedelta(days=30)
    moved_history = Lead(
        phone="2145551001",
        title="Moved history",
        state="TX",
        last_distributed_at=old,
    )
    destination_history = Lead(
        phone="2145551002",
        title="Destination history",
        state="TX",
        last_distributed_at=old,
    )
    first_fresh = Lead(
        phone="2145551003",
        title="First fresh",
        state="TX",
    )
    second_fresh = Lead(
        phone="2145551004",
        title="Second fresh",
        state="TX",
    )
    session.add_all(
        [
            moved_history,
            destination_history,
            first_fresh,
            second_fresh,
        ]
    )
    session.flush()
    session.add_all(
        [
            DistributionEvent(
                lead_id=moved_history.id,
                agent_id=moved.id,
                agency_id=origin.id,
                customer_name=moved.name,
                agency_name=origin.name,
                delivered_at=old,
                source="test",
            ),
            DistributionEvent(
                lead_id=destination_history.id,
                agent_id=destination_member.id,
                agency_id=destination.id,
                customer_name=destination_member.name,
                agency_name=destination.name,
                delivered_at=old,
                source="test",
            ),
        ]
    )
    session.commit()

    client = _admin_client(session, settings)
    try:
        preview = client.get(
            f"/api/admin/customers/{moved.id}/agency-assignment-preview",
            params={"agency_id": destination.id},
        )
        assert preview.status_code == 200
        assert preview.json()["consequences"] == {
            "customerHistoryBlockedForDestination": 1,
            "destinationHistoryBlockedForCustomer": 1,
            "historyMergeIsPermanent": True,
        }

        unconfirmed = client.post(
            f"/api/admin/customers/{moved.id}/agency-assignment",
            json={
                "agency_id": destination.id,
                "reason": "Consolidating the servicing team.",
                "confirmed": False,
            },
        )
        assert unconfirmed.status_code == 409

        assigned = client.post(
            f"/api/admin/customers/{moved.id}/agency-assignment",
            json={
                "agency_id": destination.id,
                "reason": "Consolidating the servicing team.",
                "confirmed": True,
            },
        )
        assert assigned.status_code == 200
        assert assigned.json()["customer"]["agencyId"] == destination.id

        moved_request = make_request(session, moved, 1)
        moved_result = allocate_request(session, moved_request.id, settings)
        session.commit()
        assert moved_result.allocated == 1
        assert session.scalar(
            select(DistributionEvent.lead_id).where(
                DistributionEvent.request_id == moved_request.id
            )
        ) == first_fresh.id

        destination_request = make_request(session, destination_member, 1)
        destination_result = allocate_request(
            session,
            destination_request.id,
            settings,
        )
        session.commit()
        assert destination_result.allocated == 1
        assert session.scalar(
            select(DistributionEvent.lead_id).where(
                DistributionEvent.request_id == destination_request.id
            )
        ) == second_fresh.id

        unrelated_request = make_request(session, unrelated_member, 1)
        unrelated_result = allocate_request(
            session,
            unrelated_request.id,
            settings,
        )
        session.commit()
        assert unrelated_result.allocated == 1
        assert session.scalar(
            select(DistributionEvent.lead_id).where(
                DistributionEvent.request_id == unrelated_request.id
            )
        ) == moved_history.id
    finally:
        app.dependency_overrides.clear()


def test_repeated_reassignment_keeps_original_snapshots_and_transitively_merges_history(
    session,
    settings,
):
    first = Agency(slug="first", name="First Agency")
    second = Agency(slug="second", name="Second Agency")
    third = Agency(slug="third", name="Third Agency")
    unrelated = Agency(slug="separate", name="Separate Agency")
    moved = Customer(slug="traveler", name="Traveler", agency=first)
    second_member = Customer(
        slug="second-member",
        name="Second Member",
        agency=second,
    )
    third_member = Customer(
        slug="third-member",
        name="Third Member",
        agency=third,
    )
    separate_member = Customer(
        slug="separate-member",
        name="Separate Member",
        agency=unrelated,
    )
    session.add_all(
        [
            first,
            second,
            third,
            unrelated,
            moved,
            second_member,
            third_member,
            separate_member,
        ]
    )
    session.flush()
    old = datetime.now(timezone.utc) - timedelta(days=30)
    first_lead = Lead(
        phone="2145552001",
        title="First history",
        state="TX",
        last_distributed_at=old,
    )
    second_lead = Lead(
        phone="2145552002",
        title="Second history",
        state="TX",
        last_distributed_at=old,
    )
    third_lead = Lead(
        phone="2145552003",
        title="Third history",
        state="TX",
        last_distributed_at=old,
    )
    separate_lead = Lead(
        phone="2145552004",
        title="Separate history",
        state="TX",
        last_distributed_at=old,
    )
    fresh = Lead(
        phone="2145552005",
        title="Fresh",
        state="TX",
        last_distributed_at=old,
    )
    session.add_all(
        [first_lead, second_lead, third_lead, separate_lead, fresh]
    )
    session.flush()
    events = [
        DistributionEvent(
            lead_id=first_lead.id,
            agent_id=moved.id,
            agency_id=first.id,
            customer_name=moved.name,
            agency_name=first.name,
            delivered_at=old,
            source="test",
        ),
        DistributionEvent(
            lead_id=second_lead.id,
            agent_id=second_member.id,
            agency_id=second.id,
            customer_name=second_member.name,
            agency_name=second.name,
            delivered_at=old,
            source="test",
        ),
        DistributionEvent(
            lead_id=third_lead.id,
            agent_id=third_member.id,
            agency_id=third.id,
            customer_name=third_member.name,
            agency_name=third.name,
            delivered_at=old,
            source="test",
        ),
        DistributionEvent(
            lead_id=separate_lead.id,
            agent_id=separate_member.id,
            agency_id=unrelated.id,
            customer_name=separate_member.name,
            agency_name=unrelated.name,
            delivered_at=old,
            source="test",
        ),
    ]
    session.add_all(events)
    session.commit()

    client = _admin_client(session, settings)
    try:
        for destination, reason in (
            (second, "First permanent merge."),
            (third, "Second permanent merge."),
        ):
            response = client.post(
                f"/api/admin/customers/{moved.id}/agency-assignment",
                json={
                    "agency_id": destination.id,
                    "reason": reason,
                    "confirmed": True,
                },
            )
            assert response.status_code == 200

        merged_key = moved.permanent_history_key
        assert {
            first.permanent_history_key,
            second.permanent_history_key,
            third.permanent_history_key,
            second_member.permanent_history_key,
            third_member.permanent_history_key,
            merged_key,
        } == {merged_key}
        assert unrelated.permanent_history_key != merged_key
        assert separate_member.permanent_history_key != merged_key

        memberships = list(
            session.scalars(
                select(AgencyMembershipHistory)
                .where(AgencyMembershipHistory.customer_id == moved.id)
                .order_by(AgencyMembershipHistory.started_at)
            )
        )
        assert [item.agency_id for item in memberships] == [
            first.id,
            second.id,
            third.id,
        ]
        assert [item.ended_at is None for item in memberships] == [
            False,
            False,
            True,
        ]

        for event, agency in zip(
            events,
            (first, second, third, unrelated),
            strict=True,
        ):
            session.refresh(event)
            assert event.agency_id == agency.id
            assert event.agency_name == agency.name

        request = make_request(session, moved, 1)
        result = allocate_request(session, request.id, settings)
        session.commit()
        assert result.allocated == 1
        assert session.scalar(
            select(DistributionEvent.lead_id).where(
                DistributionEvent.request_id == request.id
            )
        ) == separate_lead.id

        audits = list(
            session.scalars(
                select(AuditEntry)
                .where(
                    AuditEntry.action
                    == "customer_agency_assignment_changed"
                )
                .order_by(AuditEntry.created_at)
            )
        )
        assert [audit.reason for audit in audits] == [
            "First permanent merge.",
            "Second permanent merge.",
        ]
    finally:
        app.dependency_overrides.clear()


def test_agency_directory_details_creation_and_lifecycle_contracts(
    session,
    settings,
):
    agency = Agency(slug="harbor", name="Harbor Agency")
    customer = Customer(
        slug="harbor-customer",
        name="Harbor Customer",
        agency=agency,
    )
    session.add_all([agency, customer])
    session.commit()
    client = _admin_client(session, settings)
    try:
        created = client.post(
            "/api/admin/agencies",
            json={
                "name": "New Agency",
                "reason": "Opened a new internal servicing group.",
            },
        )
        assert created.status_code == 201
        assert created.json()["slug"] == "new-agency"

        directory = client.get(
            "/api/admin/agencies/directory",
            params={"q": "harbor", "status": "active"},
        )
        assert directory.status_code == 200
        assert directory.json()["matched"] == 1
        row = directory.json()["agencies"][0]
        assert row["currentMembers"] == 1
        assert row["sharedHistory"]["customers"] == 1

        details = client.get(
            f"/api/admin/agencies/{agency.id}/details"
        )
        assert details.status_code == 200
        assert details.json()["agency"]["name"] == "Harbor Agency"
        assert details.json()["members"][0]["name"] == "Harbor Customer"
        assert details.json()["deletion"]["dependencies"]["customers"] == 1

        bypass = client.patch(
            f"/api/admin/customers/{customer.id}",
            json={
                "name": customer.name,
                "agency_id": None,
                "active": True,
                "reason": "Unsafe direct reassignment.",
            },
        )
        assert bypass.status_code == 409
        assert "preview" in bypass.json()["detail"].lower()
    finally:
        app.dependency_overrides.clear()
