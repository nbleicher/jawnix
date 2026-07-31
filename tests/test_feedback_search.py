from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from jawnix.api import app
from jawnix.auth import Principal, require_principal
from jawnix.database import get_db
from jawnix.models import (
    Agent,
    CustomerProfile,
    DistributionEvent,
    Lead,
    LeadRequest,
    RequestStatus,
    utcnow,
)


def test_customer_searches_own_delivered_batches_by_partial_name(session):
    user_id = uuid.uuid4()
    customer = Agent(slug="search-customer", name="Search Customer")
    profile = CustomerProfile(
        user_id=user_id,
        email="search@example.com",
        licensed_states=["TX"],
        customer=customer,
        mapping_confirmed_at=utcnow(),
    )
    lead = Lead(phone="2145551200", title="Acme Roofing", state="TX")
    session.add_all([customer, profile, lead])
    session.flush()
    batch = LeadRequest(
        user_id=user_id,
        customer=customer,
        lead_count=1,
        states_snapshot=["TX"],
        state_mode="all_saved",
        delivery_email=profile.email,
        status=RequestStatus.delivered.value,
    )
    session.add(batch)
    session.flush()
    event = DistributionEvent(
        lead_id=lead.id,
        customer_id=customer.id,
        customer_name=customer.name,
        request_id=batch.id,
        phone=lead.phone,
        title=lead.title,
        state=lead.state,
        delivered_at=utcnow(),
        source="batch",
    )
    session.add(event)
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
        response = TestClient(app).post(
            "/api/me/feedback/search",
            json={"query": "roof"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == [
        {
            "distributionEventId": event.id,
            "businessName": "Acme Roofing",
            "phone": "2145551200",
            "deliveredAt": event.delivered_at.isoformat(),
            "batchId": str(batch.id),
            "currentDisposition": None,
        }
    ]


def test_search_never_exposes_another_customers_or_undelivered_leads(session):
    my_user_id = uuid.uuid4()
    their_user_id = uuid.uuid4()
    mine = Agent(slug="mine-search", name="Mine")
    theirs = Agent(slug="theirs-search", name="Theirs")
    my_profile = CustomerProfile(
        user_id=my_user_id,
        email="mine-search@example.com",
        licensed_states=["TX"],
        customer=mine,
        mapping_confirmed_at=utcnow(),
    )
    their_profile = CustomerProfile(
        user_id=their_user_id,
        email="theirs-search@example.com",
        licensed_states=["TX"],
        customer=theirs,
        mapping_confirmed_at=utcnow(),
    )
    my_lead = Lead(phone="2145551201", title="My Roofing", state="TX")
    their_lead = Lead(
        phone="2145551202", title="Private Roofing", state="TX"
    )
    waiting_lead = Lead(
        phone="2145551203", title="Undelivered Roofing", state="TX"
    )
    session.add_all(
        [
            mine,
            theirs,
            my_profile,
            their_profile,
            my_lead,
            their_lead,
            waiting_lead,
        ]
    )
    session.flush()
    my_batch = LeadRequest(
        user_id=my_user_id,
        customer=mine,
        lead_count=1,
        states_snapshot=["TX"],
        state_mode="all_saved",
        delivery_email=my_profile.email,
        status=RequestStatus.delivered.value,
    )
    their_batch = LeadRequest(
        user_id=their_user_id,
        customer=theirs,
        lead_count=1,
        states_snapshot=["TX"],
        state_mode="all_saved",
        delivery_email=their_profile.email,
        status=RequestStatus.delivered.value,
    )
    waiting_batch = LeadRequest(
        user_id=my_user_id,
        customer=mine,
        lead_count=1,
        states_snapshot=["TX"],
        state_mode="all_saved",
        delivery_email=my_profile.email,
        status=RequestStatus.processing.value,
    )
    session.add_all([my_batch, their_batch, waiting_batch])
    session.flush()
    events = [
        DistributionEvent(
            lead_id=my_lead.id,
            customer_id=mine.id,
            customer_name=mine.name,
            request_id=my_batch.id,
            phone=my_lead.phone,
            title=my_lead.title,
            state=my_lead.state,
            delivered_at=utcnow(),
            source="batch",
        ),
        DistributionEvent(
            lead_id=their_lead.id,
            customer_id=theirs.id,
            customer_name=theirs.name,
            request_id=their_batch.id,
            phone=their_lead.phone,
            title=their_lead.title,
            state=their_lead.state,
            delivered_at=utcnow(),
            source="batch",
        ),
        DistributionEvent(
            lead_id=waiting_lead.id,
            customer_id=mine.id,
            customer_name=mine.name,
            request_id=waiting_batch.id,
            phone=waiting_lead.phone,
            title=waiting_lead.title,
            state=waiting_lead.state,
            delivered_at=utcnow(),
            source="batch",
        ),
    ]
    session.add_all(events)
    session.commit()

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[require_principal] = lambda: Principal(
        user_id=my_user_id,
        email=my_profile.email,
        role="customer",
        csrf="test",
    )
    try:
        client = TestClient(app)
        by_phone = client.post(
            "/api/me/feedback/search", json={"query": "55512"}
        )
        wildcard = client.post(
            "/api/me/feedback/search", json={"query": "%%"}
        )
    finally:
        app.dependency_overrides.clear()

    assert by_phone.status_code == 200
    assert [item["distributionEventId"] for item in by_phone.json()] == [
        events[0].id
    ]
    assert wildcard.status_code == 200
    assert wildcard.json() == []
