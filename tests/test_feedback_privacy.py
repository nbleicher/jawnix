"""Customer Feedback must not become a lookup oracle over private data (#53).

`tests/test_api.py` already pins the lookup endpoint: invalid, absent, and
another Customer's phone all answer identically. This file pins the two
endpoints that take a distribution event id instead of a phone.

They matter for a subtler reason. `_customer_distribution` raises two
*different* 404s — "Customer was not found." and "Delivered Lead was not
found." — and `_customer_feedback_distribution` exists only to collapse both
back into the single feedback failure. That collapse is one `except` clause
away from being lost, and losing it would let a Customer enumerate which
distribution event ids exist by reading the message.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from jawnix.api import app
from jawnix.auth import Principal, require_principal
from jawnix.config import Settings, get_settings
from jawnix.database import get_db
from jawnix.models import (
    Agent,
    CustomerProfile,
    DistributionEvent,
    Lead,
    utcnow,
)


#: The one response every feedback failure must produce, whatever the cause.
INDISTINGUISHABLE = (404, {"detail": "No delivered Lead found."})


def scenario(session):
    """One Customer with a delivered Lead, and a second Customer with theirs."""
    mine = Agent(slug="mine", name="My Insurance")
    theirs = Agent(slug="theirs", name="Other Insurance")
    session.add_all([mine, theirs])
    session.flush()

    user_id = uuid.uuid4()
    session.add(
        CustomerProfile(
            user_id=user_id,
            email="mine@example.com",
            licensed_states=["TX"],
            customer=mine,
            mapping_confirmed_at=utcnow(),
        )
    )
    my_lead = Lead(phone="2145550001", title="My Roofing", state="TX")
    their_lead = Lead(phone="2145550002", title="Their Plumbing", state="TX")
    session.add_all([my_lead, their_lead])
    session.flush()

    my_event = DistributionEvent(
        lead_id=my_lead.id,
        customer_id=mine.id,
        customer_name=mine.name,
        phone=my_lead.phone,
        title=my_lead.title,
        state=my_lead.state,
        delivered_at=utcnow(),
        source="batch",
    )
    their_event = DistributionEvent(
        lead_id=their_lead.id,
        customer_id=theirs.id,
        customer_name=theirs.name,
        phone=their_lead.phone,
        title=their_lead.title,
        state=their_lead.state,
        delivered_at=utcnow(),
        source="batch",
    )
    session.add_all([my_event, their_event])
    session.commit()

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[get_settings] = lambda: Settings()
    app.dependency_overrides[require_principal] = lambda: Principal(
        user_id=user_id,
        email="mine@example.com",
        role="customer",
        csrf="test",
    )
    return TestClient(app), my_event, their_event


def observed(response) -> tuple[int, dict]:
    return response.status_code, response.json()


class TestHistoryIsNotAnOracle:
    def test_another_customers_event_is_indistinguishable_from_an_absent_one(
        self, session
    ):
        client, _, their_event = scenario(session)
        try:
            theirs = client.get(
                f"/api/me/distributions/{their_event.id}/dispositions"
            )
            absent = client.get("/api/me/distributions/98765/dispositions")
        finally:
            app.dependency_overrides.clear()

        assert observed(theirs) == INDISTINGUISHABLE
        assert observed(absent) == INDISTINGUISHABLE

    def test_my_own_event_is_readable(self, session):
        """The privacy assertions would pass trivially if nothing worked."""
        client, my_event, _ = scenario(session)
        try:
            mine = client.get(
                f"/api/me/distributions/{my_event.id}/dispositions"
            )
        finally:
            app.dependency_overrides.clear()

        assert mine.status_code == 200


class TestSubmissionResponseShape:
    """Pins the shape the receipt is rendered from.

    The browser doubles for this endpoint are hand-written, so nothing on the
    frontend side can notice if the real response stops matching them. This
    test is what those doubles are checked against: change the response and
    this fails, which is the prompt to change the doubles too.
    """

    def submit(self, client, event_id: int, **extra):
        return client.post(
            "/api/me/feedback",
            json={
                "distribution_event_id": event_id,
                "disposition": "no_contact",
                **extra,
            },
        )

    def test_the_transition_is_nested_not_returned_flat(self, session):
        client, my_event, _ = scenario(session)
        try:
            body = self.submit(client, my_event.id).json()
        finally:
            app.dependency_overrides.clear()

        assert set(body) == {
            "distributionEventId",
            "currentDisposition",
            "transition",
            "qualityRating",
            "reportId",
            "eligibilityHoldId",
        }
        assert set(body["transition"]) >= {"id", "disposition", "createdAt"}
        assert body["currentDisposition"] == "no_contact"

    def test_an_omitted_quality_rating_comes_back_null(self, session):
        client, my_event, _ = scenario(session)
        try:
            body = self.submit(client, my_event.id).json()
        finally:
            app.dependency_overrides.clear()

        assert body["qualityRating"] is None

    def test_a_quality_rating_is_echoed_so_a_receipt_can_confirm_it(
        self, session
    ):
        client, my_event, _ = scenario(session)
        try:
            body = self.submit(
                client, my_event.id, quality_rating="poor"
            ).json()
        finally:
            app.dependency_overrides.clear()

        assert body["qualityRating"]["kind"] == "poor"
        # Poor rates the Lead and nothing more (ADR 0011 / CONTEXT.md).
        assert body["reportId"] is None
        assert body["eligibilityHoldId"] is None

    def test_a_holding_disposition_echoes_both_controls(self, session):
        client, my_event, _ = scenario(session)
        try:
            body = client.post(
                "/api/me/feedback",
                json={
                    "distribution_event_id": my_event.id,
                    "disposition": "invalid_phone",
                },
            ).json()
        finally:
            app.dependency_overrides.clear()

        assert body["reportId"] is not None
        assert body["eligibilityHoldId"] is not None

    def test_wrong_business_echoes_a_report_and_no_hold(self, session):
        client, my_event, _ = scenario(session)
        try:
            body = client.post(
                "/api/me/feedback",
                json={
                    "distribution_event_id": my_event.id,
                    "disposition": "wrong_business",
                },
            ).json()
        finally:
            app.dependency_overrides.clear()

        assert body["reportId"] is not None
        assert body["eligibilityHoldId"] is None


class TestSubmissionIsNotAnOracle:
    def submit(self, client, event_id: int):
        return client.post(
            "/api/me/feedback",
            json={
                "distribution_event_id": event_id,
                "disposition": "no_contact",
            },
        )

    def test_writing_against_another_customers_event_is_refused_identically(
        self, session
    ):
        client, _, their_event = scenario(session)
        try:
            theirs = self.submit(client, their_event.id)
            absent = self.submit(client, 98765)
        finally:
            app.dependency_overrides.clear()

        assert observed(theirs) == INDISTINGUISHABLE
        assert observed(absent) == INDISTINGUISHABLE

    def test_a_refused_submission_writes_nothing(self, session):
        """An oracle by side effect is still an oracle."""
        from jawnix.models import LeadDispositionTransition

        client, _, their_event = scenario(session)
        try:
            self.submit(client, their_event.id)
        finally:
            app.dependency_overrides.clear()

        assert session.query(LeadDispositionTransition).count() == 0

    def test_the_two_failure_causes_are_worded_the_same(self, session):
        """`_customer_distribution` has two distinct 404s; both must collapse.

        A missing CustomerProfile and a non-matching event take different
        branches. If either stops being normalized, the wording differs and the
        difference is the oracle.
        """
        client, _, their_event = scenario(session)
        try:
            not_my_event = self.submit(client, their_event.id)
            app.dependency_overrides[require_principal] = lambda: Principal(
                # A signed-in identity with no CustomerProfile at all.
                user_id=uuid.uuid4(),
                email="stranger@example.com",
                role="customer",
                csrf="test",
            )
            no_profile = self.submit(client, their_event.id)
        finally:
            app.dependency_overrides.clear()

        assert observed(not_my_event) == observed(no_profile)
        assert observed(no_profile) == INDISTINGUISHABLE
