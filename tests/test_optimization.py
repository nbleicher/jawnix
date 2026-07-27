from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from jawnix.models import (
    Agent,
    CustomerProfile,
    DailySourcePerformance,
    DistributionEvent,
    Lead,
    LeadDispositionState,
    LeadDispositionTransition,
    LeadOutcome,
    NightlyReview,
    PerformanceSuggestionNote,
    SourceNicheMapping,
    SourceRecommendation,
)
from jawnix.optimization import (
    analyze_nightly_performance,
    materialize_source_identity,
    recommendation_action,
)


def _event(
    session,
    customer: Agent,
    *,
    segment: str,
    state: str = "PA",
    days_ago: int = 1,
    disposition: str = "positive_response",
    quality: str = "good",
) -> DistributionEvent:
    delivered_at = datetime(2026, 7, 27, 8, tzinfo=timezone.utc) - timedelta(
        days=days_ago
    )
    lead = Lead(
        phone=f"215{session.query(Lead).count() + 1:07d}",
        title=f"{segment} lead",
        state=state,
    )
    session.add(lead)
    session.flush()
    event = DistributionEvent(
        lead_id=lead.id,
        customer_id=customer.id,
        customer_name=customer.name,
        phone=lead.phone,
        title=lead.title,
        state=state,
        source_kind="google_maps",
        source_segment_key=segment,
        source_niche="Roofing",
        delivered_at=delivered_at,
    )
    session.add(event)
    session.flush()
    transition = LeadDispositionTransition(
        distribution_event_id=event.id,
        customer_id=customer.id,
        actor_user_id=uuid.uuid4(),
        disposition=disposition,
        created_at=delivered_at + timedelta(hours=1),
    )
    session.add(transition)
    session.flush()
    session.add(
        LeadDispositionState(
            distribution_event_id=event.id,
            current_transition_id=transition.id,
            current_disposition=disposition,
            updated_at=transition.created_at,
        )
    )
    if quality:
        session.add(
            LeadOutcome(
                distribution_event_id=event.id,
                customer_id=customer.id,
                kind=quality,
                metric="quality",
                created_at=delivered_at + timedelta(hours=2),
            )
        )
    return event


def test_source_identity_normalizes_keyword_and_state():
    assert materialize_source_identity("  Roof Repair  ", "pa") == (
        "PA::roof repair"
    )
    with pytest.raises(ValueError):
        materialize_source_identity("Roofing", "Pennsylvania")


@pytest.mark.parametrize(
    ("target", "peers", "expected"),
    [
        (
            {
                "worked": 200,
                "rated": 60,
                "positive": 90,
                "booked": 45,
                "critical_negative": 4,
            },
            [
                {
                    "worked": 200,
                    "rated": 60,
                    "positive": 30,
                    "booked": 10,
                    "critical_negative": 5,
                },
                {
                    "worked": 200,
                    "rated": 60,
                    "positive": 32,
                    "booked": 11,
                    "critical_negative": 4,
                },
            ],
            "expand",
        ),
        (
            {
                "worked": 200,
                "rated": 60,
                "positive": 12,
                "booked": 3,
                "critical_negative": 8,
            },
            [
                {
                    "worked": 200,
                    "rated": 60,
                    "positive": 70,
                    "booked": 30,
                    "critical_negative": 7,
                },
                {
                    "worked": 200,
                    "rated": 60,
                    "positive": 68,
                    "booked": 28,
                    "critical_negative": 8,
                },
            ],
            "reduce",
        ),
        (
            {
                "worked": 200,
                "rated": 60,
                "positive": 10,
                "booked": 2,
                "critical_negative": 70,
            },
            [
                {
                    "worked": 200,
                    "rated": 60,
                    "positive": 72,
                    "booked": 30,
                    "critical_negative": 5,
                },
                {
                    "worked": 200,
                    "rated": 60,
                    "positive": 68,
                    "booked": 28,
                    "critical_negative": 6,
                },
            ],
            "pause",
        ),
    ],
)
def test_confidence_gated_actions(target, peers, expected):
    result = recommendation_action(target, peers)
    assert result["action"] == expected
    assert result["positiveResponse"]["confidenceInterval"]
    assert result["peerSegmentCount"] == 2


def test_mixed_or_ineligible_evidence_is_notes_only():
    insufficient = recommendation_action(
        {
            "worked": 99,
            "rated": 30,
            "positive": 80,
            "booked": 40,
            "critical_negative": 0,
        },
        [],
    )
    assert insufficient["action"] is None
    assert insufficient["eligibility"] == "insufficient_worked_leads"

    mixed = recommendation_action(
        {
            "worked": 200,
            "rated": 60,
            "positive": 41,
            "booked": 12,
            "critical_negative": 9,
        },
        [
            {
                "worked": 200,
                "rated": 60,
                "positive": 40,
                "booked": 11,
                "critical_negative": 8,
            },
            {
                "worked": 200,
                "rated": 60,
                "positive": 39,
                "booked": 10,
                "critical_negative": 7,
            },
        ],
    )
    assert mixed["action"] is None
    assert mixed["eligibility"] == "eligible_no_action"


def test_appointment_booked_strengthens_directional_positive_signal():
    result = recommendation_action(
        {
            "worked": 300,
            "rated": 60,
            "positive": 67,
            "booked": 75,
            "critical_negative": 4,
        },
        [
            {
                "worked": 300,
                "rated": 60,
                "positive": 60,
                "booked": 20,
                "critical_negative": 5,
            },
            {
                "worked": 300,
                "rated": 60,
                "positive": 61,
                "booked": 22,
                "critical_negative": 4,
            },
        ],
    )
    assert result["positiveResponse"]["confidenceInterval"][0] <= 0
    assert result["appointmentBooked"]["confidenceInterval"][0] > 0
    assert result["action"] == "expand"


def test_nightly_snapshots_are_immutable_and_late_feedback_changes_only_later_day(
    session,
):
    customer = Agent(slug="optimizer", name="Optimizer")
    session.add(customer)
    session.flush()
    session.add(
        SourceNicheMapping(
            segment_key="PA::roof repair",
            state="PA",
            keyword="roof repair",
            niche="Roofing",
            confirmed=True,
            proposal_source="migration",
        )
    )
    event = _event(
        session,
        customer,
        segment="PA::roof repair",
    )
    review_one = NightlyReview(
        review_date=date(2026, 7, 26),
        status="complete",
        summary={},
    )
    session.add(review_one)
    session.flush()
    first = analyze_nightly_performance(
        session,
        review_one,
        as_of=datetime(2026, 7, 27, 9, tzinfo=timezone.utc),
    )
    session.flush()
    assert len(first) == 1
    assert first[0].counts["worked"] == 1
    assert first[0].counts["positive"] == 1

    first_again = analyze_nightly_performance(
        session,
        review_one,
        as_of=datetime(2026, 7, 27, 10, tzinfo=timezone.utc),
    )
    assert first_again[0].id == first[0].id

    previous = session.get(LeadDispositionState, event.id)
    correction = LeadDispositionTransition(
        distribution_event_id=event.id,
        customer_id=customer.id,
        actor_user_id=uuid.uuid4(),
        disposition="appointment_canceled",
        previous_transition_id=previous.current_transition_id,
        created_at=datetime(2026, 7, 28, 8, tzinfo=timezone.utc),
    )
    session.add(correction)
    session.flush()
    previous.current_transition_id = correction.id
    previous.current_disposition = correction.disposition
    previous.updated_at = correction.created_at
    review_two = NightlyReview(
        review_date=date(2026, 7, 28),
        status="complete",
        summary={},
    )
    session.add(review_two)
    session.flush()
    second = analyze_nightly_performance(
        session,
        review_two,
        as_of=datetime(2026, 7, 28, 9, tzinfo=timezone.utc),
    )
    session.flush()

    session.refresh(first[0])
    assert first[0].counts["canceled"] == 0
    assert second[0].counts["canceled"] == 1
    assert second[0].counts["positive"] == 1
    assert session.scalar(
        select(PerformanceSuggestionNote).where(
            PerformanceSuggestionNote.snapshot_id == second[0].id
        )
    )


def test_denied_recommendation_stays_quiet_until_material_evidence(session):
    recommendation = SourceRecommendation(
        niche="Roofing",
        segment_key="PA::roof repair",
        action="reduce",
        evidence={
            "counts": {"worked": 120, "rated": 35},
            "configurationVersion": 4,
            "niche": "Roofing",
        },
        evidence_checksum="a" * 64,
        status="denied",
    )
    session.add(recommendation)
    session.flush()
    from jawnix.optimization import denial_still_suppresses

    assert denial_still_suppresses(
        recommendation,
        action="reduce",
        niche="Roofing",
        configuration_version=4,
        counts={"worked": 144, "rated": 44},
    )
    assert not denial_still_suppresses(
        recommendation,
        action="reduce",
        niche="Roofing",
        configuration_version=4,
        counts={"worked": 145, "rated": 44},
    )
    assert not denial_still_suppresses(
        recommendation,
        action="expand",
        niche="Roofing",
        configuration_version=4,
        counts={"worked": 120, "rated": 35},
    )
