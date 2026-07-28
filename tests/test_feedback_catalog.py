"""The Lead Disposition catalog the Customer Feedback screen renders (#53).

The load-bearing test here is
``test_the_catalog_cannot_drift_from_what_is_enforced``. The screen must state
a disposition's consequence *before* the Customer commits to it, and those
consequences are irreversible. Copy maintained separately from
``apply_disposition_controls`` would eventually disagree with it, and the
failure mode is telling someone their answer is harmless when it places an
Eligibility Hold. So the catalog is derived from the same constants the
controls are materialized from, and this file pins that.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from jawnix.api import app
from jawnix.auth import Principal, require_principal
from jawnix.config import Settings, get_settings
from jawnix.database import get_db
from jawnix.dispositions import CATALOG, GROUP_ORDER, catalog_payload
from jawnix.feedback import HOLD_DISPOSITIONS, REPORT_DISPOSITIONS
from jawnix.schemas import FeedbackCreate


CUSTOMER_ID = uuid.uuid4()


def as_customer(session) -> TestClient:
    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[get_settings] = lambda: Settings()
    app.dependency_overrides[require_principal] = lambda: Principal(
        user_id=CUSTOMER_ID,
        email="customer@example.com",
        role="customer",
        csrf="test",
    )
    return TestClient(app)


class TestCatalogIntegrity:
    def test_the_catalog_cannot_drift_from_what_is_enforced(self):
        """Every stated consequence matches the rule that materializes it."""
        for entry in CATALOG:
            creates_report = entry.disposition in REPORT_DISPOSITIONS
            creates_hold = entry.disposition in HOLD_DISPOSITIONS
            assert entry.creates_report is creates_report, entry.disposition
            assert entry.creates_hold is creates_hold, entry.disposition

    def test_a_hold_is_never_claimed_without_a_report(self):
        """A hold hangs off a report, so claiming one without the other lies."""
        for entry in CATALOG:
            if entry.creates_hold:
                assert entry.creates_report, entry.disposition

    def test_wrong_business_reports_without_holding(self):
        """The distinction easiest to collapse, stated explicitly."""
        entry = next(
            item for item in CATALOG if item.disposition == "wrong_business"
        )
        assert entry.creates_report is True
        assert entry.creates_hold is False

    def test_invalid_phone_and_do_not_contact_report_and_hold(self):
        for disposition in ("invalid_phone", "do_not_contact"):
            entry = next(
                item for item in CATALOG if item.disposition == disposition
            )
            assert entry.creates_report is True, disposition
            assert entry.creates_hold is True, disposition

    def test_every_accepted_disposition_is_offered(self):
        """The screen cannot omit a disposition the contract accepts."""
        accepted = set(
            FeedbackCreate.model_fields["disposition"]
            .metadata[0]
            .pattern.strip("^$()")
            .replace("|", " ")
            .split()
        )
        assert {entry.disposition for entry in CATALOG} == accepted

    def test_no_disposition_is_offered_that_would_be_refused(self):
        for entry in CATALOG:
            payload = {
                "distribution_event_id": 1,
                "disposition": entry.disposition,
                "note": "A note, in case this disposition requires one.",
            }
            # Constructing the real request model is the check: an offered
            # disposition the contract rejects would raise here.
            FeedbackCreate(**payload)

    def test_only_other_requires_a_note(self):
        for entry in CATALOG:
            assert entry.requires_note is (entry.disposition == "other"), (
                entry.disposition
            )

    def test_every_group_is_named_and_ordered(self):
        assert [group for group, _ in GROUP_ORDER] == [
            "contact_result",
            "positive_progress",
            "appointment_follow_up",
            "data_compliance_problem",
            "other",
        ]
        grouped = {entry.group for entry in CATALOG}
        assert grouped == {group for group, _ in GROUP_ORDER}


class TestCatalogEndpoint:
    def test_the_catalog_is_served_to_a_signed_in_customer(self, session):
        client = as_customer(session)
        try:
            response = client.get("/api/me/feedback/dispositions")
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        body = response.json()
        assert [group["group"] for group in body["groups"]] == [
            "contact_result",
            "positive_progress",
            "appointment_follow_up",
            "data_compliance_problem",
            "other",
        ]
        flattened = [
            option
            for group in body["groups"]
            for option in group["options"]
        ]
        assert len(flattened) == len(CATALOG)

    def test_the_served_consequences_name_report_and_hold_distinctly(
        self, session
    ):
        client = as_customer(session)
        try:
            body = client.get("/api/me/feedback/dispositions").json()
        finally:
            app.dependency_overrides.clear()

        options = {
            option["disposition"]: option
            for group in body["groups"]
            for option in group["options"]
        }
        assert options["wrong_business"]["createsReport"] is True
        assert options["wrong_business"]["createsHold"] is False
        assert options["invalid_phone"]["createsHold"] is True
        assert options["do_not_contact"]["createsHold"] is True
        # A disposition with no durable control must say nothing about one.
        assert options["no_contact"]["createsReport"] is False
        assert options["no_contact"]["createsHold"] is False
        assert options["no_contact"]["consequence"] == ""

    def test_a_quality_rating_is_never_described_as_a_consequence(
        self, session
    ):
        """Poor must not imply suppression or a Lead Report."""
        client = as_customer(session)
        try:
            body = client.get("/api/me/feedback/dispositions").json()
        finally:
            app.dependency_overrides.clear()

        rating = body["qualityRating"]
        assert rating["optional"] is True
        assert [option["value"] for option in rating["options"]] == [
            "good",
            "poor",
        ]
        for option in rating["options"]:
            assert option["createsReport"] is False
            assert option["createsHold"] is False
        combined = " ".join(
            option["description"] for option in rating["options"]
        ).lower()
        for forbidden in ("suppress", "lead report", "hold"):
            assert forbidden not in combined, forbidden


def test_the_payload_is_json_safe():
    """The catalog is served verbatim, so it must survive serialization."""
    import json

    json.dumps(catalog_payload())
