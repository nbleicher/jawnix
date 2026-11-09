"""Operator global search: Customers, Batches, and DB2 uploads, grouped.

Seam: seeded records in; ``search()`` and ``GET /api/admin/search`` out.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from jawnix.api import app
from jawnix.auth import Principal, require_admin
from jawnix.database import get_db
from jawnix.models import (
    Agent,
    CustomerProfile,
    LeadRequest,
    LeadUpload,
    ManagedAdmin,
    UserAccount,
)
from jawnix.search import search


def seed(session) -> dict:
    northstar = Agent(slug="northstar", name="Northstar Insurance")
    harbor = Agent(slug="harbor-point", name="Harbor Insurance", active=False)
    erased = Agent(
        slug="erased",
        name="Northstar Erased",
        active=False,
        deleted_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )
    session.add_all([northstar, harbor, erased])
    session.flush()
    session.add(
        UserAccount(
            auth_user_id=uuid.uuid4(), customer_id=harbor.id, email="owner@harbor.example"
        )
    )
    user_id = uuid.uuid4()
    session.add(CustomerProfile(user_id=user_id, email="river@example.com", agent=northstar))
    session.flush()
    batches = []
    for _ in range(2):
        item = LeadRequest(
            user_id=user_id,
            agent=northstar,
            lead_count=25_000,
            states_snapshot=["FL"],
            delivery_email="river@example.com",
            status="pending",
            created_at=datetime(2026, 11, 9, 9, 41, tzinfo=timezone.utc),
        )
        session.add(item)
        session.flush()
        batches.append(item)
    manager = ManagedAdmin(auth_user_id=uuid.uuid4(), email="alex@example.com", name="Alex")
    session.add(manager)
    session.flush()
    session.add_all(
        [
            LeadUpload(
                manager_id=manager.auth_user_id,
                filename="tn-agents-nov.csv",
                state="TN",
                status="published",
                path="/tmp/tn.csv",
                created_at=datetime(2026, 11, 6, tzinfo=timezone.utc),
            ),
            LeadUpload(
                operator_id=uuid.uuid4(),
                filename="fl_100%_agents.csv",
                state="FL",
                status="published",
                path="/tmp/fl.csv",
                created_at=datetime(2026, 11, 7, tzinfo=timezone.utc),
            ),
        ]
    )
    session.commit()
    return {"northstar": northstar, "harbor": harbor, "batches": batches}


def groups(result) -> dict[str, list[str]]:
    return {
        group["key"]: [item["title"] for item in group["results"]]
        for group in result["groups"]
    }


def test_customers_match_by_name_slug_or_user_account_email(session):
    records = seed(session)
    assert groups(search(session, "northstar")) == {"customers": ["Northstar Insurance"]}
    assert groups(search(session, "harbor-po")) == {"customers": ["Harbor Insurance"]}
    by_email = search(session, "owner@harbor")
    customer = by_email["groups"][0]["results"][0]
    assert customer["href"] == f"/app/admin/customers/{records['harbor'].id}"
    assert customer["detail"] == "harbor-point · owner@harbor.example · Deactivated"


def test_batches_match_by_number_and_uuid_prefix(session):
    records = seed(session)
    first, second = records["batches"]
    result = search(session, f"#{second.number}")
    assert groups(result) == {"batches": [f"Batch #{second.number} · Northstar Insurance"]}
    item = result["groups"][0]["results"][0]
    assert item["href"] == f"/app/admin/batches/{second.number}"
    assert item["detail"] == "25,000 leads · Awaiting approval · Nov 9"
    assert groups(search(session, f"REQ-{first.id.hex[:8]}"))["batches"] == [
        f"Batch #{first.number} · Northstar Insurance"
    ]


def test_uploads_match_by_file_name_and_escape_wildcards(session):
    seed(session)
    result = search(session, "agents")
    assert groups(result) == {"uploads": ["fl_100%_agents.csv", "tn-agents-nov.csv"]}
    details = [item["detail"] for item in result["groups"][0]["results"]]
    assert details == ["Operator · FL · Nov 7 · Published", "Alex · TN · Nov 6 · Published"]
    assert groups(search(session, "100%")) == {"uploads": ["fl_100%_agents.csv"]}
    # Unescaped, "_" would match the "-" in tn-agents.
    assert groups(search(session, "n_a")) == {}


def test_short_queries_return_nothing(session):
    seed(session)
    assert search(session, " n ") == {"query": "n", "groups": []}


def test_search_endpoint_groups_results_in_display_order(session):
    records = seed(session)

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=uuid.uuid4(), email="admin@example.com", role="admin", csrf="test"
    )
    try:
        body = TestClient(app).get(
            "/api/admin/search", params={"q": str(records["batches"][0].number)}
        ).json()
    finally:
        app.dependency_overrides.clear()
    assert [group["label"] for group in body["groups"]] == ["Batches"]
