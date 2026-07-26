from __future__ import annotations

import hashlib
import json
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    DistributionEvent,
    LeadOutcome,
    SourceRecommendation,
)


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
    cohorts: dict[tuple[str, str], dict] = {}
    legacy_delivered = 0
    global_counts = {
        "delivered": 0,
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
                "rated": 0,
                "good": 0,
                "poor": 0,
                "positiveResponses": 0,
                "appointmentsBooked": 0,
            },
        )
        item["delivered"] += 1
        global_counts["delivered"] += 1
        quality = active.get((event.id, "quality"))
        if quality is not None:
            item["rated"] += 1
            item[quality.kind] += 1
            global_counts["rated"] += 1
            global_counts[quality.kind] += 1
        if (event.id, "positive_response") in active:
            item["positiveResponses"] += 1
            global_counts["positiveResponses"] += 1
        if (event.id, "appointment_booked") in active:
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
        delivered = item["delivered"]
        item["goodRate"] = item["good"] / rated if rated else 0.0
        item["positiveResponseRate"] = (
            item["positiveResponses"] / delivered if delivered else 0.0
        )
        item["appointmentRate"] = (
            item["appointmentsBooked"] / delivered if delivered else 0.0
        )
        item["qualityStatus"] = (
            "ranked" if rated >= 30 else "insufficient_data"
        )
        item["conversionStatus"] = (
            "ranked" if delivered >= 100 else "insufficient_data"
        )
        results.append(item)
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
