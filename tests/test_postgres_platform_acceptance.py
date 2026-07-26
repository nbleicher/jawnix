from __future__ import annotations

import os
import sqlite3
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from jawnix.allocation import fulfill_round_robin
from jawnix.api import app
from jawnix.auth import Principal, require_principal
from jawnix.config import Settings, get_settings
from jawnix.database import get_db
from jawnix.models import (
    Agent,
    CustomerProfile,
    DistributionEvent,
    Lead,
    LeadOutcome,
    LeadRequest,
    ListingObservation,
    utcnow,
)
from jawnix.transitions import transition_request
from jawnix_data.scraper import sync_scraper


@pytest.mark.skipif(
    not os.environ.get("JAWNIX_ACCEPTANCE_DATABASE_URL"),
    reason="requires the real PostgreSQL acceptance database",
)
def test_google_maps_to_customer_feedback_acceptance(tmp_path):
    database_url = os.environ["JAWNIX_ACCEPTANCE_DATABASE_URL"]
    engine = create_engine(database_url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    run_key = uuid.uuid4().hex[:12]
    phone = f"469{int(run_key[:7], 16) % 10_000_000:07d}"
    dataset = tmp_path / "leads.db"
    with sqlite3.connect(dataset) as source:
        source.execute(
            """
            CREATE TABLE leads (
                phone TEXT,
                company TEXT,
                full_name TEXT,
                niche TEXT,
                state TEXT,
                source TEXT,
                created_at TEXT
            )
            """
        )
        source.execute(
            "INSERT INTO leads VALUES (?, ?, '', 'Roofing', 'TX', ?, ?)",
            (
                phone,
                "Acceptance Roofing",
                f"maps-{run_key}",
                "2026-07-25T02:00:00+00:00",
            ),
        )

    settings = Settings(
        JAWNIX_SCRAPER_DB_PATH=dataset,
        JAWNIX_BATCH_DIR=tmp_path / "batches",
        JAWNIX_COOKIE_SECURE=False,
        JAWNIX_SESSION_SECRET="acceptance-secret-at-least-long-enough",
    )
    with factory.begin() as session:
        sync_result = sync_scraper(session, settings)
        assert sync_result["imported"] == 1
        lead = session.scalar(select(Lead).where(Lead.phone == phone))
        assert lead is not None
        observation = session.get(
            ListingObservation,
            lead.current_listing_observation_id,
        )
        assert observation is not None and observation.valid

        customer = Agent(
            slug=f"acceptance-{run_key}",
            name="Acceptance Customer",
        )
        user_id = uuid.uuid4()
        profile = CustomerProfile(
            user_id=user_id,
            email=f"{run_key}@example.invalid",
            licensed_states=["TX"],
            agent=customer,
            mapping_confirmed_at=utcnow(),
        )
        session.add_all([customer, profile])
        session.flush()

    with factory() as session:
        def database_override():
            yield session

        principal = Principal(
            user_id=user_id,
            email=profile.email,
            role="customer",
            csrf="acceptance",
        )
        app.dependency_overrides[get_db] = database_override
        app.dependency_overrides[get_settings] = lambda: settings
        app.dependency_overrides[require_principal] = lambda: principal
        try:
            client = TestClient(app)
            created = client.post(
                "/api/me/requests",
                json={
                    "lead_count": 1,
                    "state_mode": "selected",
                    "states": ["TX"],
                },
            )
            assert created.status_code == 201
            request_id = uuid.UUID(created.json()["id"])
            transition_request(session, request_id, "approve")
            fulfill_round_robin(session, settings)
            session.commit()

            request = session.get(LeadRequest, request_id)
            assert request is not None and request.status == "generated"
            event = session.scalar(
                select(DistributionEvent).where(
                    DistributionEvent.request_id == request_id
                )
            )
            assert event is not None
            assert event.phone == phone
            assert event.source_kind == "google_maps"
            assert event.source_niche == "roofing"

            outcome = client.post(
                f"/api/me/distributions/{event.id}/outcomes",
                json={"kind": "positive_response"},
            )
            assert outcome.status_code == 201
            assert session.scalar(
                select(LeadOutcome).where(
                    LeadOutcome.distribution_event_id == event.id
                )
            ) is not None
        finally:
            app.dependency_overrides.clear()
    engine.dispose()
