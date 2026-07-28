from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from jawnix.eligibility import (
    ControlConflict,
    correct_from_report,
    describe_report,
    dismiss_report,
    lead_evidence,
    report_summaries,
    suppress_from_report,
)
from jawnix.models import (
    Agent,
    DistributionEvent,
    EligibilityHold,
    Lead,
    LeadCorrectionEvent,
    LeadDispositionTransition,
    LeadReport,
    ListingObservation,
)
from jawnix.feedback import apply_disposition_controls


ACTOR = uuid.uuid4()


def _lead(session, *, phone: str, **kwargs) -> Lead:
    lead = Lead(
        phone=phone,
        title=kwargs.pop("title", "Effective Title"),
        state=kwargs.pop("state", "PA"),
        **kwargs,
    )
    session.add(lead)
    session.flush()
    return lead


def _reported(session, disposition: str = "invalid_phone"):
    """A Lead delivered to a Customer, then reported by that Customer."""
    customer = Agent(slug=f"customer-{uuid.uuid4().hex[:8]}", name="Customer")
    lead = _lead(session, phone=f"215555{session.query(Lead).count():04d}")
    session.add(customer)
    session.flush()
    event = DistributionEvent(
        lead_id=lead.id,
        customer_id=customer.id,
        customer_name=customer.name,
        phone=lead.phone,
        title=lead.title,
        state=lead.state,
    )
    session.add(event)
    session.flush()
    transition = LeadDispositionTransition(
        distribution_event_id=event.id,
        customer_id=customer.id,
        actor_user_id=uuid.uuid4(),
        disposition=disposition,
        note="Line was disconnected",
        created_at=datetime(2026, 7, 27, 12, tzinfo=timezone.utc),
    )
    session.add(transition)
    session.flush()
    report, hold = apply_disposition_controls(session, event, transition)
    session.flush()
    return lead, event, report, hold


def _immutable_snapshot(session) -> dict:
    """Everything these controls are forbidden to rewrite."""
    return {
        "distributions": [
            (
                item.id,
                item.lead_id,
                item.agent_id,
                item.title,
                item.state,
                item.phone,
                item.listing_provenance,
                item.delivered_at,
            )
            for item in session.scalars(
                select(DistributionEvent).order_by(DistributionEvent.id)
            )
        ],
        "observations": [
            (item.id, item.lead_id, item.title, item.state, item.valid)
            for item in session.scalars(
                select(ListingObservation).order_by(ListingObservation.id)
            )
        ],
        "reports": [
            (item.id, item.reason, item.details, item.created_at)
            for item in session.scalars(
                select(LeadReport).order_by(LeadReport.created_at)
            )
        ],
    }


class TestEvidence:
    """A correction must be grounded in what it disagreed with."""

    def test_current_listing_is_the_evidence_when_one_exists(self, session):
        lead = _lead(session, phone="2155550101", title="Effective", state="PA")
        observation = ListingObservation(
            lead_id=lead.id,
            dataset_checksum="a" * 64,
            row_number=1,
            normalized_phone=lead.phone,
            title="Observed Title",
            state="NJ",
            source="roofing-pa",
            niche="Roofing",
            valid=True,
            observed_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
        )
        session.add(observation)
        session.flush()
        lead.current_listing_observation_id = observation.id
        session.flush()

        evidence = lead_evidence(session, lead)

        assert evidence.kind == "current_listing"
        assert evidence.title == "Observed Title"
        assert evidence.state == "NJ"
        assert evidence.observation_id == observation.id
        assert "Google Maps" in evidence.label

    def test_legacy_snapshot_is_the_evidence_without_a_current_listing(
        self,
        session,
    ):
        lead = _lead(
            session,
            phone="2155550102",
            title="Effective",
            legacy_title="Imported Title",
            legacy_state="TX",
        )

        evidence = lead_evidence(session, lead)

        assert evidence.kind == "legacy_snapshot"
        assert evidence.title == "Imported Title"
        assert evidence.state == "TX"
        assert evidence.observation_id is None

    def test_an_active_correction_is_the_evidence_a_new_one_supersedes(
        self,
        session,
    ):
        lead = _lead(session, phone="2155550103", legacy_title="Imported")
        correction = LeadCorrectionEvent(
            lead_id=lead.id,
            action="applied",
            title="First Override",
            state="NY",
            actor_id=str(ACTOR),
            reason="Confirmed by phone",
            based_on_kind="legacy_snapshot",
        )
        session.add(correction)
        session.flush()
        lead.active_correction_id = correction.id
        session.flush()

        evidence = lead_evidence(session, lead)

        assert evidence.kind == "prior_correction"
        assert evidence.title == "First Override"
        assert evidence.state == "NY"

    def test_a_lead_with_nothing_underneath_reports_no_evidence(self, session):
        lead = _lead(session, phone="2155550104", title="Only Effective")

        evidence = lead_evidence(session, lead)

        assert evidence.kind == "none"
        assert evidence.title == ""


class TestDistinctResolutions:
    """Dismissed, Corrected, and Suppressed are three different decisions."""

    def test_dismissal_releases_the_hold_and_changes_nothing_else(
        self,
        session,
    ):
        lead, _, report, hold = _reported(session)
        before = _immutable_snapshot(session)

        dismiss_report(session, report, actor_id=ACTOR, note="No fault found")
        session.flush()

        assert report.status == "dismissed"
        assert session.get(EligibilityHold, hold.id).active is False
        # A dismissal is a judgement about the report, not about the Lead.
        assert lead.suppressed is False
        assert lead.active_correction_id is None
        assert _immutable_snapshot(session) == before

    def test_correction_overrides_delivery_and_records_its_evidence(
        self,
        session,
    ):
        lead, _, report, hold = _reported(session, "wrong_business")
        lead.legacy_title = "Imported Title"
        lead.legacy_state = "PA"
        session.flush()
        before = _immutable_snapshot(session)

        correction = correct_from_report(
            session,
            report,
            actor_id=ACTOR,
            note="Verified with the business",
            title="Correct Business",
            state="NJ",
        )
        session.flush()

        assert report.status == "corrected"
        assert lead.active_correction_id == correction.id
        assert lead.title == "Correct Business"
        assert lead.state == "NJ"
        # The override is grounded: the row says what it disagreed with.
        assert correction.based_on_kind == "legacy_snapshot"
        assert correction.based_on_title == "Imported Title"
        assert correction.based_on_state == "PA"
        assert lead.suppressed is False
        assert _immutable_snapshot(session) == before
        assert hold is None or session.get(EligibilityHold, hold.id) is None

    def test_suppression_makes_the_lead_ineligible_and_keeps_its_reason(
        self,
        session,
    ):
        lead, _, report, hold = _reported(session)
        before = _immutable_snapshot(session)

        suppress_from_report(
            session,
            report,
            actor_id=ACTOR,
            note="Do-not-contact request",
        )
        session.flush()

        assert report.status == "suppressed"
        assert lead.suppressed is True
        assert lead.suppression_reason == "Do-not-contact request"
        # Suppression is not a correction: nothing about the Lead's delivered
        # values changes.
        assert lead.active_correction_id is None
        assert session.get(EligibilityHold, hold.id).active is False
        assert _immutable_snapshot(session) == before

    def test_a_resolved_report_cannot_be_resolved_again(self, session):
        _, _, report, _ = _reported(session)
        dismiss_report(session, report, actor_id=ACTOR, note="Reviewed")
        session.flush()

        with pytest.raises(ControlConflict):
            suppress_from_report(
                session,
                report,
                actor_id=ACTOR,
                note="Changed my mind",
            )

    def test_correction_requires_something_to_change(self, session):
        _, _, report, _ = _reported(session, "wrong_business")

        with pytest.raises(ControlConflict):
            correct_from_report(
                session,
                report,
                actor_id=ACTOR,
                note="Nothing to change",
                title=None,
                state=None,
            )


class TestReadModel:
    def test_report_details_carry_the_report_customer_event_and_controls(
        self,
        session,
    ):
        lead, event, report, hold = _reported(session)

        detail = describe_report(session, report)

        assert detail["id"] == str(report.id)
        assert detail["status"] == "open"
        assert detail["customer"]["id"] == event.agent_id
        assert detail["distributionEvent"]["id"] == event.id
        assert detail["distributionEvent"]["title"] == event.title
        assert detail["controls"]["eligibilityHeld"] is True
        assert detail["controls"]["holdId"] == str(hold.id)
        assert detail["controls"]["suppressed"] is False
        assert detail["evidence"]["kind"] in {
            "none",
            "legacy_snapshot",
            "current_listing",
        }
        assert detail["lead"]["id"] == lead.id
        # Every offered action states its own distinct consequence.
        offered = {item["name"]: item for item in detail["actions"]}
        assert set(offered) == {"dismiss", "correct", "suppress"}
        assert len({item["consequence"] for item in offered.values()}) == 3
        assert offered["correct"]["requiresOverride"] is True
        assert offered["suppress"]["destructive"] is True

    def test_the_hold_release_rule_is_stated_rather_than_implied(
        self,
        session,
    ):
        _, _, report, _ = _reported(session)

        detail = describe_report(session, report)

        assert "administrator" in detail["controls"]["holdRelease"].lower()
        assert detail["controls"]["holdReleasableByCustomer"] is False

    def test_the_queue_lists_open_reports_and_omits_resolved_ones(
        self,
        session,
    ):
        _, _, first, _ = _reported(session)
        _, _, second, _ = _reported(session, "wrong_business")
        dismiss_report(session, second, actor_id=ACTOR, note="Reviewed")
        session.flush()

        queued = report_summaries(session)

        assert [item["id"] for item in queued] == [str(first.id)]
        assert queued[0]["eligibilityHeld"] is True

    def test_a_suppressed_lead_offers_restoration_from_its_report(
        self,
        session,
    ):
        """The screen that suppressed the Lead is where it is undone."""
        _, _, report, _ = _reported(session)
        assert describe_report(session, report)["controls"]["actions"] == []

        suppress_from_report(
            session,
            report,
            actor_id=ACTOR,
            note="Do-not-contact request",
        )
        session.flush()

        controls = describe_report(session, report)["controls"]
        assert [item["name"] for item in controls["actions"]] == ["restore"]
        assert "guarantee" in controls["actions"][0]["consequence"]
