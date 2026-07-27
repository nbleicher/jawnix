from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    DailySourcePerformance,
    DistributionEvent,
    LeadDispositionTransition,
    LeadOutcome,
    NightlyReview,
    PerformanceSuggestionNote,
    ScraperConfiguration,
    SourceNicheMapping,
    SourceRecommendation,
)


MIN_WORKED = 100
MIN_RATED = 30
MIN_PEERS = 2
WINDOW_DAYS = 90
Z_95 = 1.959963984540054


def materialize_source_identity(keyword: str, state: str) -> str:
    normalized_keyword = re.sub(r"\s+", " ", keyword.strip()).casefold()
    normalized_state = state.strip().upper()
    if not normalized_keyword:
        raise ValueError("Source Segment keyword is required.")
    if not re.fullmatch(r"[A-Z]{2}", normalized_state):
        raise ValueError("Source Segment state must be a two-letter code.")
    return f"{normalized_state}::{normalized_keyword}"


def _wilson(successes: int, total: int) -> tuple[float, float]:
    if total <= 0:
        return (0.0, 1.0)
    proportion = successes / total
    z2 = Z_95 * Z_95
    denominator = 1 + z2 / total
    center = (proportion + z2 / (2 * total)) / denominator
    margin = (
        Z_95
        * math.sqrt(
            proportion * (1 - proportion) / total
            + z2 / (4 * total * total)
        )
        / denominator
    )
    return (max(0.0, center - margin), min(1.0, center + margin))


def _difference_interval(
    target_successes: int,
    target_total: int,
    peer_successes: int,
    peer_total: int,
) -> dict:
    target_rate = (
        target_successes / target_total if target_total else 0.0
    )
    peer_rate = peer_successes / peer_total if peer_total else 0.0
    target_low, target_high = _wilson(target_successes, target_total)
    peer_low, peer_high = _wilson(peer_successes, peer_total)
    difference = target_rate - peer_rate
    return {
        "targetRate": target_rate,
        "peerRate": peer_rate,
        "estimatedLift": difference,
        "confidenceInterval": [
            difference
            - math.sqrt(
                (target_rate - target_low) ** 2
                + (peer_high - peer_rate) ** 2
            ),
            difference
            + math.sqrt(
                (target_high - target_rate) ** 2
                + (peer_rate - peer_low) ** 2
            ),
        ],
    }


def recommendation_action(target: dict, peers: list[dict]) -> dict:
    if int(target.get("worked", 0)) < MIN_WORKED:
        return {
            "action": None,
            "eligibility": "insufficient_worked_leads",
            "peerSegmentCount": len(peers),
        }
    if int(target.get("rated", 0)) < MIN_RATED:
        return {
            "action": None,
            "eligibility": "insufficient_quality_ratings",
            "peerSegmentCount": len(peers),
        }
    eligible_peers = [
        peer
        for peer in peers
        if int(peer.get("worked", 0)) >= MIN_WORKED
        and int(peer.get("rated", 0)) >= MIN_RATED
    ]
    if len(eligible_peers) < MIN_PEERS:
        return {
            "action": None,
            "eligibility": "insufficient_peer_data",
            "peerSegmentCount": len(eligible_peers),
        }

    peer_worked = sum(int(item["worked"]) for item in eligible_peers)
    positive = _difference_interval(
        int(target.get("positive", 0)),
        int(target["worked"]),
        sum(int(item.get("positive", 0)) for item in eligible_peers),
        peer_worked,
    )
    booked = _difference_interval(
        int(target.get("booked", 0)),
        int(target["worked"]),
        sum(int(item.get("booked", 0)) for item in eligible_peers),
        peer_worked,
    )
    critical = _difference_interval(
        int(target.get("critical_negative", 0)),
        int(target["worked"]),
        sum(
            int(item.get("critical_negative", 0))
            for item in eligible_peers
        ),
        peer_worked,
    )
    positive_low, positive_high = positive["confidenceInterval"]
    booked_low, _ = booked["confidenceInterval"]
    critical_low, _ = critical["confidenceInterval"]
    action = None
    rationale = "Evidence is mixed or statistically inconclusive."
    if positive_low > 0 and critical_low <= 0:
        action = "expand"
        rationale = (
            "Positive Response is significantly stronger with no "
            "significantly worse critical guardrail."
        )
    elif (
        positive["estimatedLift"] > 0
        and booked_low > 0
        and critical_low <= 0
    ):
        action = "expand"
        rationale = (
            "Positive Response is directionally stronger and "
            "Appointment Booked is significantly stronger, with no "
            "significantly worse critical guardrail."
        )
    elif positive_high < 0 and critical_low > 0:
        action = "pause"
        rationale = (
            "Positive Response is significantly weaker and a critical "
            "negative guardrail is significantly worse."
        )
    elif positive_high < 0:
        action = "reduce"
        rationale = (
            "Positive Response is significantly weaker without a "
            "significantly worse critical guardrail."
        )
    return {
        "action": action,
        "eligibility": "eligible" if action else "eligible_no_action",
        "peerSegmentCount": len(eligible_peers),
        "positiveResponse": positive,
        "appointmentBooked": booked,
        "criticalNegative": critical,
        "rationale": rationale,
    }


def denial_still_suppresses(
    recommendation: SourceRecommendation,
    *,
    action: str,
    niche: str,
    configuration_version: int | None,
    counts: dict,
) -> bool:
    if recommendation.status != "denied":
        return False
    evidence = recommendation.evidence or {}
    previous_counts = evidence.get("counts") or {}
    return (
        recommendation.action == action
        and str(evidence.get("niche") or recommendation.niche) == niche
        and evidence.get("configurationVersion") == configuration_version
        and int(counts.get("worked", 0))
        - int(previous_counts.get("worked", 0))
        < 25
        and int(counts.get("rated", 0))
        - int(previous_counts.get("rated", 0))
        < 10
    )


def _active_outcomes(
    outcomes: list[LeadOutcome],
) -> dict[tuple[int, str], LeadOutcome]:
    superseded = {
        item.supersedes_outcome_id
        for item in outcomes
        if item.supersedes_outcome_id is not None
    }
    return {
        (item.distribution_event_id, item.metric): item
        for item in outcomes
        if item.id not in superseded
    }


def _segment_counts(
    events: list[DistributionEvent],
    transitions: list[LeadDispositionTransition],
    active_outcomes: dict[tuple[int, str], LeadOutcome],
) -> dict[str, dict]:
    transitions_by_event: dict[int, list[LeadDispositionTransition]] = (
        defaultdict(list)
    )
    for transition in transitions:
        transitions_by_event[transition.distribution_event_id].append(
            transition
        )
    result: dict[str, dict] = {}
    for event in events:
        counts = result.setdefault(
            event.source_segment_key,
            {
                "delivered": 0,
                "worked": 0,
                "rated": 0,
                "good": 0,
                "poor": 0,
                "positive": 0,
                "booked": 0,
                "canceled": 0,
                "no_show": 0,
                "invalid": 0,
                "wrong_business": 0,
                "do_not_contact": 0,
                "critical_negative": 0,
            },
        )
        counts["delivered"] += 1
        history = transitions_by_event.get(event.id, [])
        legacy_positive = (
            (event.id, "positive_response") in active_outcomes
        )
        legacy_booked = (
            (event.id, "appointment_booked") in active_outcomes
        )
        if history or legacy_positive or legacy_booked:
            counts["worked"] += 1
        dispositions = {item.disposition for item in history}
        if (
            dispositions & {"positive_response", "appointment_booked"}
            or legacy_positive
        ):
            counts["positive"] += 1
        if "appointment_booked" in dispositions or legacy_booked:
            counts["booked"] += 1
        for disposition, key in (
            ("appointment_canceled", "canceled"),
            ("appointment_no_show", "no_show"),
            ("invalid_phone", "invalid"),
            ("wrong_business", "wrong_business"),
            ("do_not_contact", "do_not_contact"),
        ):
            if disposition in dispositions:
                counts[key] += 1
        counts["critical_negative"] += sum(
            int(value in dispositions)
            for value in (
                "appointment_no_show",
                "invalid_phone",
                "do_not_contact",
            )
        )
        rating = active_outcomes.get((event.id, "quality"))
        if rating is not None:
            counts["rated"] += 1
            if rating.kind in {"good", "poor"}:
                counts[rating.kind] += 1
    return result


def _rates(counts: dict) -> dict:
    worked = int(counts["worked"])
    rated = int(counts["rated"])
    return {
        "positiveResponse": counts["positive"] / worked if worked else 0.0,
        "appointmentBooked": counts["booked"] / worked if worked else 0.0,
        "appointmentCanceled": counts["canceled"] / worked if worked else 0.0,
        "appointmentNoShow": counts["no_show"] / worked if worked else 0.0,
        "invalidPhone": counts["invalid"] / worked if worked else 0.0,
        "wrongBusiness": counts["wrong_business"] / worked if worked else 0.0,
        "doNotContact": (
            counts["do_not_contact"] / worked if worked else 0.0
        ),
        "good": counts["good"] / rated if rated else 0.0,
        "poor": counts["poor"] / rated if rated else 0.0,
    }


def _checksum(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _note_text(
    segment_key: str,
    counts: dict,
    analysis: dict,
) -> tuple[str, str]:
    eligibility = analysis["eligibility"]
    action = analysis.get("action")
    if action:
        template = f"recommend_{action}"
        text = (
            f"{segment_key}: {action.title()} suggested. "
            f"{analysis['rationale']} Evidence: {counts['worked']} worked "
            f"Leads and {counts['rated']} Quality Ratings."
        )
    else:
        template = eligibility
        text = (
            f"{segment_key}: No acquisition change. "
            f"Status: {eligibility.replace('_', ' ')}. Evidence: "
            f"{counts['worked']} worked Leads and "
            f"{counts['rated']} Quality Ratings."
        )
    return template, text


def analyze_nightly_performance(
    session: Session,
    review: NightlyReview,
    *,
    as_of: datetime | None = None,
) -> list[DailySourcePerformance]:
    as_of = as_of or datetime.now(timezone.utc)
    snapshot_date = review.review_date or as_of.date()
    existing = list(
        session.scalars(
            select(DailySourcePerformance)
            .where(
                DailySourcePerformance.snapshot_date == snapshot_date
            )
            .order_by(DailySourcePerformance.segment_key)
        )
    )
    if existing:
        return existing

    window_start = as_of - timedelta(days=WINDOW_DAYS)
    events = list(
        session.scalars(
            select(DistributionEvent).where(
                DistributionEvent.source_kind == "google_maps",
                DistributionEvent.delivered_at >= window_start,
                DistributionEvent.delivered_at <= as_of,
            )
        )
    )
    event_ids = [item.id for item in events]
    transitions = (
        list(
            session.scalars(
                select(LeadDispositionTransition).where(
                    LeadDispositionTransition.distribution_event_id.in_(
                        event_ids
                    ),
                    LeadDispositionTransition.created_at <= as_of,
                )
            )
        )
        if event_ids
        else []
    )
    outcomes = (
        list(
            session.scalars(
                select(LeadOutcome).where(
                    LeadOutcome.distribution_event_id.in_(event_ids),
                    LeadOutcome.created_at <= as_of,
                )
            )
        )
        if event_ids
        else []
    )
    mappings = {
        item.segment_key: item
        for item in session.scalars(select(SourceNicheMapping))
    }
    counts_by_segment = _segment_counts(
        events,
        transitions,
        _active_outcomes(outcomes),
    )
    segment_keys = sorted(set(counts_by_segment) | set(mappings))
    latest_configuration = session.scalar(
        select(ScraperConfiguration)
        .order_by(ScraperConfiguration.version.desc())
        .limit(1)
    )
    prior_rows: dict[str, DailySourcePerformance] = {}
    for item in session.scalars(
        select(DailySourcePerformance)
        .where(DailySourcePerformance.snapshot_date < snapshot_date)
        .order_by(DailySourcePerformance.snapshot_date.desc())
    ):
        prior_rows.setdefault(item.segment_key, item)
    drafts: list[dict] = []
    for segment_key in segment_keys:
        mapping = mappings.get(segment_key)
        state, _, keyword = segment_key.partition("::")
        counts = counts_by_segment.get(
            segment_key,
            {
                "delivered": 0,
                "worked": 0,
                "rated": 0,
                "good": 0,
                "poor": 0,
                "positive": 0,
                "booked": 0,
                "canceled": 0,
                "no_show": 0,
                "invalid": 0,
                "wrong_business": 0,
                "do_not_contact": 0,
                "critical_negative": 0,
            },
        )
        rates = _rates(counts)
        previous = prior_rows.get(segment_key)
        drafts.append(
            {
                "segment_key": segment_key,
                "state": mapping.state if mapping else state,
                "keyword": mapping.keyword if mapping else keyword,
                "niche": mapping.niche if mapping else "",
                "niche_confirmed": bool(mapping and mapping.confirmed),
                "counts": counts,
                "rates": rates,
                "trend": {
                    key: (
                        value - float(previous.rates.get(key, 0.0))
                        if previous is not None
                        else None
                    )
                    for key, value in rates.items()
                },
            }
        )

    created: list[DailySourcePerformance] = []
    for draft in drafts:
        peers = [
            item["counts"]
            for item in drafts
            if item["segment_key"] != draft["segment_key"]
            and item["state"] == draft["state"]
            and item["niche"] == draft["niche"]
            and item["niche_confirmed"]
        ]
        analysis = (
            recommendation_action(draft["counts"], peers)
            if draft["niche_confirmed"]
            else {
                "action": None,
                "eligibility": "unconfirmed_niche",
                "peerSegmentCount": 0,
            }
        )
        evidence = {
            "snapshotDate": snapshot_date.isoformat(),
            "segmentKey": draft["segment_key"],
            "niche": draft["niche"],
            "nicheConfirmed": draft["niche_confirmed"],
            "counts": draft["counts"],
            "rates": draft["rates"],
            "analysis": analysis,
            "configurationVersion": (
                latest_configuration.version
                if latest_configuration is not None
                else None
            ),
        }
        snapshot = DailySourcePerformance(
            nightly_review_id=review.id,
            snapshot_date=snapshot_date,
            segment_key=draft["segment_key"],
            state=draft["state"],
            keyword=draft["keyword"],
            niche=draft["niche"],
            niche_confirmed=draft["niche_confirmed"],
            window_started_at=window_start,
            window_ended_at=as_of,
            counts=draft["counts"],
            rates=draft["rates"],
            intervals={
                key: value
                for key, value in analysis.items()
                if key
                in {
                    "positiveResponse",
                    "appointmentBooked",
                    "criticalNegative",
                }
            },
            trend=draft["trend"],
            eligibility=analysis["eligibility"],
            action_state=analysis.get("action") or "notes_only",
            evidence_checksum=_checksum(evidence),
        )
        session.add(snapshot)
        session.flush()
        template, text = _note_text(
            draft["segment_key"],
            draft["counts"],
            analysis,
        )
        session.add(
            PerformanceSuggestionNote(
                snapshot_id=snapshot.id,
                segment_key=draft["segment_key"],
                template_key=template,
                text=text,
                evidence=evidence,
                evidence_checksum=_checksum(
                    {"snapshot": snapshot.evidence_checksum, "text": text}
                ),
            )
        )
        action = analysis.get("action")
        if action:
            denied = session.scalar(
                select(SourceRecommendation)
                .where(
                    SourceRecommendation.segment_key
                    == draft["segment_key"],
                    SourceRecommendation.status == "denied",
                )
                .order_by(SourceRecommendation.decided_at.desc())
                .limit(1)
            )
            if denied is None or not denial_still_suppresses(
                denied,
                action=action,
                niche=draft["niche"],
                configuration_version=evidence["configurationVersion"],
                counts=draft["counts"],
            ):
                session.add(
                    SourceRecommendation(
                        nightly_review_id=review.id,
                        snapshot_id=snapshot.id,
                        niche=draft["niche"],
                        segment_key=draft["segment_key"],
                        action=action,
                        evidence=evidence,
                        evidence_checksum=_checksum(
                            {
                                "snapshot": snapshot.evidence_checksum,
                                "action": action,
                            }
                        ),
                        configuration_version=evidence[
                            "configurationVersion"
                        ],
                    )
                )
        created.append(snapshot)
    session.flush()
    return created
