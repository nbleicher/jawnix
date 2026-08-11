from __future__ import annotations

import hashlib
import json
from collections import defaultdict

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .models import (
    DistributionEvent,
    LeadDispositionTransition,
    LeadOutcome,
    SourceRecommendation,
)


def _keyword_from_segment(segment: str) -> str:
    return segment.partition("::")[2] or segment


def _active_outcomes_subquery():
    """Active LeadOutcome rows as a SELECT — ids never leave the database."""

    superseded = select(LeadOutcome.supersedes_outcome_id).where(
        LeadOutcome.supersedes_outcome_id.is_not(None)
    )
    return (
        select(
            LeadOutcome.distribution_event_id.label("event_id"),
            LeadOutcome.metric,
            LeadOutcome.kind,
        )
        .where(LeadOutcome.id.not_in(superseded))
        .subquery()
    )


def keyword_outcome_analytics(
    session: Session,
    *,
    keywords: list[str] | None = None,
) -> list[dict[str, object]]:
    """Customer outcome analytics by keyword, never Scraper yield.

    Positive outcomes are divided by every delivered lead. A keyword with no
    delivery receives ``None`` rather than a misleading zero-percent score.

    Aggregates in SQL so the Keywords workspace does not materialize every
    DistributionEvent (or outcome id) into Python — that path blanked the page
    at production scale after the Scraper cutover, and binding millions of ids
    as query parameters hits the same 65,535-parameter ceiling that broke
    Agency assignment preview.
    """

    delivered_rows = session.execute(
        select(
            DistributionEvent.source_segment_key,
            func.count().label("delivered"),
        )
        .where(DistributionEvent.source_kind == "google_maps")
        .group_by(DistributionEvent.source_segment_key)
    ).all()

    active_outcomes = _active_outcomes_subquery()
    positive_events = (
        select(LeadDispositionTransition.distribution_event_id.label("event_id"))
        .where(
            LeadDispositionTransition.disposition.in_(
                ("positive_response", "appointment_booked")
            )
        )
        .union(
            select(active_outcomes.c.event_id).where(
                active_outcomes.c.metric == "positive_response"
            )
        )
        .subquery()
    )
    positive_by_segment = {
        str(segment): int(count)
        for segment, count in session.execute(
            select(
                DistributionEvent.source_segment_key,
                func.count().label("positive"),
            )
            .where(
                DistributionEvent.source_kind == "google_maps",
                DistributionEvent.id.in_(select(positive_events.c.event_id)),
            )
            .group_by(DistributionEvent.source_segment_key)
        )
    }

    rows: dict[str, dict[str, object]] = {}
    for segment, delivered in delivered_rows:
        keyword = _keyword_from_segment(str(segment))
        key = keyword.casefold()
        row = rows.setdefault(
            key,
            {"keyword": keyword, "delivered": 0, "positive": 0},
        )
        row["delivered"] = int(row["delivered"]) + int(delivered)
        row["positive"] = int(row["positive"]) + positive_by_segment.get(
            str(segment), 0
        )
    for keyword in keywords or []:
        rows.setdefault(
            keyword.casefold(),
            {"keyword": keyword, "delivered": 0, "positive": 0},
        )
    result = []
    for row in rows.values():
        delivered = int(row["delivered"])
        positive = int(row["positive"])
        result.append(
            {
                **row,
                "positives_per_delivered": (
                    positive / delivered if delivered else None
                ),
            }
        )
    return sorted(
        result,
        key=lambda row: (
            row["positives_per_delivered"] is None,
            -(float(row["positives_per_delivered"] or 0)),
            -int(row["delivered"]),
            str(row["keyword"]).casefold(),
        ),
    )


def source_performance_summary(session: Session) -> dict:
    """All-time Source Performance totals without loading every event row.

    The Admin Source Performance screen needs ``global`` / ``legacy`` beside
    the nightly ``rows``. Loading every DistributionEvent into Python for that
    summary hung the route at production scale. Cohorts stay available from
    :func:`source_performance_snapshot` for offline analysis; this path only
    returns the aggregates the screen renders.
    """

    google_delivered = (
        session.scalar(
            select(func.count())
            .select_from(DistributionEvent)
            .where(DistributionEvent.source_kind == "google_maps")
        )
        or 0
    )
    legacy_delivered = (
        session.scalar(
            select(func.count())
            .select_from(DistributionEvent)
            .where(DistributionEvent.source_kind != "google_maps")
        )
        or 0
    )

    active_outcomes = _active_outcomes_subquery()
    google_events = (
        select(DistributionEvent.id.label("event_id"))
        .where(DistributionEvent.source_kind == "google_maps")
        .subquery()
    )
    disposition_events = (
        select(
            LeadDispositionTransition.distribution_event_id.label("event_id")
        )
        .distinct()
        .subquery()
    )
    legacy_signal_events = (
        select(active_outcomes.c.event_id)
        .where(
            active_outcomes.c.metric.in_(
                ("positive_response", "appointment_booked")
            )
        )
        .distinct()
        .subquery()
    )
    worked = (
        session.scalar(
            select(func.count())
            .select_from(google_events)
            .where(
                or_(
                    google_events.c.event_id.in_(
                        select(disposition_events.c.event_id)
                    ),
                    google_events.c.event_id.in_(
                        select(legacy_signal_events.c.event_id)
                    ),
                )
            )
        )
        or 0
    )

    rated_good = (
        session.scalar(
            select(func.count())
            .select_from(google_events)
            .where(
                google_events.c.event_id.in_(
                    select(active_outcomes.c.event_id).where(
                        active_outcomes.c.metric == "quality",
                        active_outcomes.c.kind == "good",
                    )
                )
            )
        )
        or 0
    )
    rated_poor = (
        session.scalar(
            select(func.count())
            .select_from(google_events)
            .where(
                google_events.c.event_id.in_(
                    select(active_outcomes.c.event_id).where(
                        active_outcomes.c.metric == "quality",
                        active_outcomes.c.kind == "poor",
                    )
                )
            )
        )
        or 0
    )
    rated = rated_good + rated_poor

    positive = (
        session.scalar(
            select(func.count())
            .select_from(google_events)
            .where(
                or_(
                    google_events.c.event_id.in_(
                        select(
                            LeadDispositionTransition.distribution_event_id
                        ).where(
                            LeadDispositionTransition.disposition.in_(
                                ("positive_response", "appointment_booked")
                            )
                        )
                    ),
                    google_events.c.event_id.in_(
                        select(active_outcomes.c.event_id).where(
                            active_outcomes.c.metric == "positive_response"
                        )
                    ),
                )
            )
        )
        or 0
    )
    booked = (
        session.scalar(
            select(func.count())
            .select_from(google_events)
            .where(
                or_(
                    google_events.c.event_id.in_(
                        select(
                            LeadDispositionTransition.distribution_event_id
                        ).where(
                            LeadDispositionTransition.disposition
                            == "appointment_booked"
                        )
                    ),
                    google_events.c.event_id.in_(
                        select(active_outcomes.c.event_id).where(
                            active_outcomes.c.metric == "appointment_booked"
                        )
                    ),
                )
            )
        )
        or 0
    )

    return {
        "cohorts": [],
        "segments": [],
        "global": {
            "delivered": int(google_delivered),
            "worked": int(worked),
            "rated": int(rated),
            "good": int(rated_good),
            "poor": int(rated_poor),
            "positiveResponses": int(positive),
            "appointmentsBooked": int(booked),
            "rates": {
                "good": (rated_good / rated if rated else 0.0),
                "positiveResponse": (
                    positive / worked if worked else 0.0
                ),
                "appointmentBooked": (
                    booked / worked if worked else 0.0
                ),
            },
            "prescriptive": False,
        },
        "legacy": {
            "delivered": int(legacy_delivered),
            "excludedFromRecommendations": True,
        },
    }


def source_performance_snapshot(session: Session) -> dict:
    events = list(
        session.scalars(
            select(DistributionEvent).order_by(DistributionEvent.id)
        )
    )
    outcomes = list(
        session.scalars(
            select(LeadOutcome).order_by(
                LeadOutcome.created_at,
                LeadOutcome.id,
            )
        )
    )
    superseded_ids = {
        item.supersedes_outcome_id
        for item in outcomes
        if item.supersedes_outcome_id is not None
    }
    active = {
        (item.distribution_event_id, item.metric): item
        for item in outcomes
        if item.id not in superseded_ids
    }
    transitions = list(
        session.scalars(
            select(LeadDispositionTransition).order_by(
                LeadDispositionTransition.created_at,
                LeadDispositionTransition.id,
            )
        )
    )
    dispositions_by_event: dict[int, set[str]] = defaultdict(set)
    for transition in transitions:
        dispositions_by_event[transition.distribution_event_id].add(
            transition.disposition
        )
    cohorts: dict[tuple[str, str], dict] = {}
    legacy_delivered = 0
    global_counts = {
        "delivered": 0,
        "worked": 0,
        "rated": 0,
        "good": 0,
        "poor": 0,
        "positiveResponses": 0,
        "appointmentsBooked": 0,
    }
    for event in events:
        if event.source_kind != "google_maps":
            legacy_delivered += 1
            continue
        period = (
            event.distribution_period
            or event.delivered_at.strftime("%Y-%m")
        )
        key = (event.source_segment_key, period)
        item = cohorts.setdefault(
            key,
            {
                "segment": event.source_segment_key,
                "niche": event.source_niche,
                "distributionPeriod": period,
                "delivered": 0,
                "worked": 0,
                "rated": 0,
                "good": 0,
                "poor": 0,
                "positiveResponses": 0,
                "appointmentsBooked": 0,
            },
        )
        item["delivered"] += 1
        global_counts["delivered"] += 1
        dispositions = dispositions_by_event.get(event.id, set())
        legacy_positive = (event.id, "positive_response") in active
        legacy_booked = (event.id, "appointment_booked") in active
        if dispositions or legacy_positive or legacy_booked:
            item["worked"] += 1
            global_counts["worked"] += 1
        quality = active.get((event.id, "quality"))
        if quality is not None:
            item["rated"] += 1
            item[quality.kind] += 1
            global_counts["rated"] += 1
            global_counts[quality.kind] += 1
        if (
            dispositions & {"positive_response", "appointment_booked"}
            or legacy_positive
        ):
            item["positiveResponses"] += 1
            global_counts["positiveResponses"] += 1
        if "appointment_booked" in dispositions or legacy_booked:
            item["appointmentsBooked"] += 1
            global_counts["appointmentsBooked"] += 1
    results = []
    for item in sorted(
        cohorts.values(),
        key=lambda value: (
            value["segment"],
            value["distributionPeriod"],
        ),
    ):
        rated = item["rated"]
        worked = item["worked"]
        item["goodRate"] = item["good"] / rated if rated else 0.0
        item["positiveResponseRate"] = (
            item["positiveResponses"] / worked if worked else 0.0
        )
        item["appointmentRate"] = (
            item["appointmentsBooked"] / worked if worked else 0.0
        )
        item["qualityStatus"] = (
            "ranked" if rated >= 30 else "insufficient_data"
        )
        item["conversionStatus"] = (
            "ranked" if worked >= 100 else "insufficient_data"
        )
        results.append(item)
    global_worked = global_counts["worked"]
    global_rated = global_counts["rated"]
    global_counts["rates"] = {
        "good": (
            global_counts["good"] / global_rated if global_rated else 0.0
        ),
        "positiveResponse": (
            global_counts["positiveResponses"] / global_worked
            if global_worked
            else 0.0
        ),
        "appointmentBooked": (
            global_counts["appointmentsBooked"] / global_worked
            if global_worked
            else 0.0
        ),
    }
    global_counts["prescriptive"] = False
    return {
        "cohorts": results,
        # Compatibility during the product migration.
        "segments": results,
        "global": global_counts,
        "legacy": {
            "delivered": legacy_delivered,
            "excludedFromRecommendations": True,
        },
    }


def build_source_recommendations(session: Session) -> list[SourceRecommendation]:
    # The worked-lead prescriptive engine is intentionally dormant. Its code
    # and evidence stay available for an explicit future product decision, but
    # this entry point cannot create recommendations while dormant.
    from .optimization import WORKED_LEADS_PRESCRIPTIVE_ENABLED

    if not WORKED_LEADS_PRESCRIPTIVE_ENABLED:
        return []
    snapshot = source_performance_snapshot(session)
    by_niche: dict[str, list[dict]] = defaultdict(list)
    for cohort in snapshot["cohorts"]:
        if (
            cohort["qualityStatus"] == "ranked"
            and cohort["conversionStatus"] == "ranked"
        ):
            by_niche[cohort["niche"]].append(cohort)
    created: list[SourceRecommendation] = []
    for niche, cohorts in by_niche.items():
        by_segment: dict[str, list[dict]] = defaultdict(list)
        for cohort in cohorts:
            by_segment[cohort["segment"]].append(cohort)
        evidence_rows = []
        for segment, rows in by_segment.items():
            delivered = sum(item["delivered"] for item in rows)
            rated = sum(item["rated"] for item in rows)
            evidence_rows.append(
                {
                    "segment": segment,
                    "periods": sorted(
                        item["distributionPeriod"] for item in rows
                    ),
                    "delivered": delivered,
                    "rated": rated,
                    "good": sum(item["good"] for item in rows),
                    "positiveResponses": sum(
                        item["positiveResponses"] for item in rows
                    ),
                    "appointmentsBooked": sum(
                        item["appointmentsBooked"] for item in rows
                    ),
                }
            )
        if len(evidence_rows) < 2:
            continue
        for row in evidence_rows:
            row["score"] = (
                row["good"] / row["rated"]
                + row["positiveResponses"] / row["delivered"]
                + row["appointmentsBooked"] / row["delivered"]
            )
        ranked = sorted(
            evidence_rows,
            key=lambda item: (item["score"], item["segment"]),
        )
        proposals = [
            ("reduce" if ranked[0]["score"] > 0 else "pause", ranked[0]),
            ("expand", ranked[-1]),
        ]
        for action, row in proposals:
            evidence = {
                "niche": niche,
                "comparisonScope": "same_niche",
                "proposedAction": action,
                "segment": row,
                "peerSegments": evidence_rows,
            }
            checksum = hashlib.sha256(
                json.dumps(
                    evidence,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            existing = session.scalar(
                select(SourceRecommendation).where(
                    SourceRecommendation.evidence_checksum == checksum
                )
            )
            if existing is not None:
                continue
            recommendation = SourceRecommendation(
                niche=niche,
                segment_key=row["segment"],
                action=action,
                evidence=evidence,
                evidence_checksum=checksum,
            )
            session.add(recommendation)
            created.append(recommendation)
    session.flush()
    return created
