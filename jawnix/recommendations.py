from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    AuditEntry,
    ScraperConfiguration,
    SourceRecommendation,
    SourceSegment,
)
from .optimization import materialize_source_identity


class RecommendationDecisionError(ValueError):
    pass


def _scheduled_acquisition(now: datetime) -> datetime:
    scheduled = (now + timedelta(days=1)).replace(
        hour=8,
        minute=0,
        second=0,
        microsecond=0,
    )
    return scheduled


def _next_segments(
    latest: ScraperConfiguration,
    recommendation: SourceRecommendation,
) -> list[SourceSegment]:
    segments: list[SourceSegment] = []
    target_found = False
    for segment in latest.segments:
        parameters = dict(segment.parameters)
        if segment.key == recommendation.segment_key:
            target_found = True
            parameters["recommendation_action"] = recommendation.action
            parameters["status"] = {
                "expand": "active",
                "reduce": "reduced",
                "pause": "paused",
            }[recommendation.action]
            parameters["cadence_multiplier"] = {
                "expand": 1.0,
                "reduce": 0.5,
                "pause": 0.0,
            }[recommendation.action]
            parameters["relative_weight"] = {
                "expand": 1.25,
                "reduce": 0.5,
                "pause": 0.0,
            }[recommendation.action]
            parameters["enabled"] = recommendation.action != "pause"
            parameters["adjacent_generation_enabled"] = (
                recommendation.action == "expand"
            )
        segments.append(
            SourceSegment(
                key=segment.key,
                niche=segment.niche,
                query=segment.query,
                geography=segment.geography,
                parameters=parameters,
            )
        )
    if not target_found:
        raise RecommendationDecisionError(
            "The recommended Source Segment is not in the current "
            "configuration."
        )
    if recommendation.action == "expand":
        evidence = recommendation.evidence or {}
        state = str(
            evidence.get("state")
            or recommendation.segment_key.partition("::")[0]
        ).upper()
        existing_keys = {item.key for item in segments}
        for keyword in evidence.get("adjacentKeywords") or []:
            key = materialize_source_identity(str(keyword), state)
            if key in existing_keys:
                continue
            segments.append(
                SourceSegment(
                    key=key,
                    niche=recommendation.niche,
                    query=str(keyword).strip(),
                    geography=state,
                    parameters={
                        "status": "active",
                        "cadence_multiplier": 1.0,
                        "niche_confirmed": True,
                        "seed_segment_key": recommendation.segment_key,
                        "performance_state": "insufficient_data",
                    },
                )
            )
            existing_keys.add(key)
    return segments


def decide_recommendation(
    session: Session,
    recommendation_id: uuid.UUID,
    action: str,
    *,
    actor_id: str,
    reason: str,
    apply_enabled: bool,
    shadow_mode: bool,
    now: datetime | None = None,
) -> SourceRecommendation:
    if action not in {"approve", "deny"}:
        raise RecommendationDecisionError("Unknown decision action.")
    recommendation = session.scalar(
        select(SourceRecommendation)
        .where(SourceRecommendation.id == recommendation_id)
        .with_for_update()
    )
    if recommendation is None:
        raise LookupError("Source Recommendation was not found.")
    if recommendation.status != "pending":
        raise RecommendationDecisionError(
            "Source Recommendation was already decided."
        )
    now = now or datetime.now(timezone.utc)
    latest = session.scalar(
        select(ScraperConfiguration)
        .order_by(ScraperConfiguration.version.desc())
        .limit(1)
        .with_for_update()
    )
    if (
        recommendation.configuration_version is not None
        and (
            latest is None
            or latest.version != recommendation.configuration_version
        )
    ):
        recommendation.status = "stale"
        recommendation.decision_by = actor_id
        recommendation.decision_reason = reason
        recommendation.decided_at = now
        session.add(
            AuditEntry(
                action="source_recommendation_stale",
                target_type="source_recommendation",
                target_id=str(recommendation.id),
                actor_user_id=actor_id,
                reason=reason,
                details={
                    "evidenceChecksum": recommendation.evidence_checksum,
                    "evidenceConfigurationVersion": (
                        recommendation.configuration_version
                    ),
                    "currentConfigurationVersion": (
                        latest.version if latest is not None else None
                    ),
                },
            )
        )
        raise RecommendationDecisionError(
            "Source Recommendation is stale; production was not changed."
        )
    recommendation.decision_by = actor_id
    recommendation.decision_reason = reason
    recommendation.decided_at = now
    if action == "deny":
        recommendation.status = "denied"
    elif shadow_mode or not apply_enabled:
        recommendation.status = "approved_shadow"
    else:
        if latest is None:
            raise RecommendationDecisionError(
                "Create a Scraper Configuration first."
            )
        for scheduled in session.scalars(
            select(ScraperConfiguration)
            .where(ScraperConfiguration.status == "scheduled")
            .with_for_update()
        ):
            scheduled.status = "schedule_replaced"
        segments = _next_segments(latest, recommendation)
        canonical = json.dumps(
            {
                "segments": [
                    {
                        "key": item.key,
                        "niche": item.niche,
                        "query": item.query,
                        "geography": item.geography,
                        "parameters": item.parameters,
                    }
                    for item in segments
                ],
                "anomaly_thresholds": latest.anomaly_thresholds,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        next_configuration = ScraperConfiguration(
            version=latest.version + 1,
            checksum=hashlib.sha256(
                canonical.encode("utf-8")
            ).hexdigest(),
            status="scheduled",
            anomaly_thresholds=latest.anomaly_thresholds,
            created_by=uuid.UUID(actor_id),
            reason=reason,
            scheduled_at=_scheduled_acquisition(now),
            based_on_configuration_id=latest.id,
            segments=segments,
        )
        session.add(next_configuration)
        session.flush()
        recommendation.status = "approved"
        recommendation.resulting_configuration_id = (
            next_configuration.id
        )
    session.add(
        AuditEntry(
            action=f"source_recommendation_{recommendation.status}",
            target_type="source_recommendation",
            target_id=str(recommendation.id),
            actor_user_id=actor_id,
            reason=reason,
            details={
                "proposedAction": recommendation.action,
                "segment": recommendation.segment_key,
                "evidenceChecksum": recommendation.evidence_checksum,
                "evidenceConfigurationVersion": (
                    recommendation.configuration_version
                ),
                "resultingConfigurationId": (
                    str(recommendation.resulting_configuration_id)
                    if recommendation.resulting_configuration_id
                    else None
                ),
                "shadowMode": shadow_mode,
            },
        )
    )
    session.flush()
    return recommendation
