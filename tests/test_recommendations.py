from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from jawnix.models import (
    AuditEntry,
    ScraperConfiguration,
    SourceRecommendation,
    SourceSegment,
)
from jawnix.recommendations import (
    RecommendationDecisionError,
    decide_recommendation,
)


def _configuration(session, *, version: int = 4):
    configuration = ScraperConfiguration(
        version=version,
        checksum=str(version) * 64,
        status="active",
        anomaly_thresholds={},
        created_by=uuid.uuid4(),
        reason="Baseline",
        segments=[
            SourceSegment(
                key="PA::roof repair",
                niche="Roofing",
                query="roof repair",
                geography="PA",
                parameters={
                    "status": "active",
                    "cadence_multiplier": 1.0,
                },
            )
        ],
    )
    session.add(configuration)
    session.flush()
    return configuration


def _recommendation(session, action: str = "reduce"):
    recommendation = SourceRecommendation(
        niche="Roofing",
        segment_key="PA::roof repair",
        action=action,
        evidence={
            "state": "PA",
            "adjacentKeywords": ["roof replacement"],
        },
        evidence_checksum=action[0] * 64,
        configuration_version=4,
    )
    session.add(recommendation)
    session.flush()
    return recommendation


def test_shadow_approval_records_decision_without_configuration(session):
    _configuration(session)
    recommendation = _recommendation(session)
    decided_recommendation = decide_recommendation(
        session,
        recommendation.id,
        "approve",
        actor_id=str(uuid.uuid4()),
        reason="Review evidence",
        apply_enabled=False,
        shadow_mode=True,
    )
    assert decided_recommendation.status == "approved_shadow"
    assert session.query(ScraperConfiguration).count() == 1
    assert session.scalar(select(AuditEntry)).details["shadowMode"] is True


@pytest.mark.parametrize(
    ("action", "status", "cadence", "adjacent"),
    [
        ("expand", "active", 1.0, True),
        ("reduce", "reduced", 0.5, False),
        ("pause", "paused", 0.0, False),
    ],
)
def test_approved_action_schedules_exact_next_configuration(
    session, action, status, cadence, adjacent
):
    _configuration(session)
    recommendation = _recommendation(session, action)
    decided_recommendation = decide_recommendation(
        session,
        recommendation.id,
        "approve",
        actor_id=str(uuid.uuid4()),
        reason="Approved",
        apply_enabled=True,
        shadow_mode=False,
        now=datetime(2026, 7, 27, 12, tzinfo=timezone.utc),
    )
    assert decided_recommendation.status == "approved"
    next_configuration = session.get(
        ScraperConfiguration,
        decided_recommendation.resulting_configuration_id,
    )
    assert next_configuration.status == "scheduled"
    assert next_configuration.scheduled_at == datetime(
        2026, 7, 28, 8, tzinfo=timezone.utc
    )
    seed = next(
        item
        for item in next_configuration.segments
        if item.key == recommendation.segment_key
    )
    assert seed.parameters["status"] == status
    assert seed.parameters["cadence_multiplier"] == cadence
    assert (
        seed.parameters["adjacent_generation_enabled"] is adjacent
    )
    adjacent_rows = [
        item
        for item in next_configuration.segments
        if item.key != recommendation.segment_key
    ]
    assert bool(adjacent_rows) is adjacent
    if adjacent:
        assert adjacent_rows[0].key == "PA::roof replacement"
        assert (
            adjacent_rows[0].parameters["performance_state"]
            == "insufficient_data"
        )


def test_stale_or_duplicate_decision_never_changes_configuration(session):
    _configuration(session, version=5)
    recommendation = _recommendation(session)
    with pytest.raises(RecommendationDecisionError, match="stale"):
        decide_recommendation(
            session,
            recommendation.id,
            "approve",
            actor_id=str(uuid.uuid4()),
            reason="Stale",
            apply_enabled=True,
            shadow_mode=False,
        )
    assert recommendation.status == "stale"
    assert session.query(ScraperConfiguration).count() == 1
    with pytest.raises(
        RecommendationDecisionError, match="already decided"
    ):
        decide_recommendation(
            session,
            recommendation.id,
            "approve",
            actor_id=str(uuid.uuid4()),
            reason="Replay",
            apply_enabled=True,
            shadow_mode=False,
        )
