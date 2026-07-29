"""The administrator Acquisition workspace read model (#68).

Nightly Reviews, held Scrape Anomalies, Source Recommendations, Niche
mappings, and Scraper Configuration versions are all Jawnix-owned records with
their own endpoints already. This module gathers the subset the workspace
opens on, so one screen is one authenticated read rather than six.

It is deliberately read-only. Every decision the workspace offers routes back
to the durable command that already owns it — ``decide_scrape_anomaly`` for
anomalies, ``decide_recommendation`` for recommendations, the configuration
endpoints for versions. Nothing here decides anything, because a read model
that also decides is how a second code path starts.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    NightlyReview,
    ScrapeAnomaly,
    ScrapeSegmentResult,
    ScraperConfiguration,
    ScraperRun,
    SourceNicheMapping,
    SourceRecommendation,
)


#: The only state a record can still be acted on in. The rest are shown as
#: history so a decision that already happened is never re-offered.
PENDING = "pending"

#: Enough Nightly Reviews to see a trend without paging the whole history.
NIGHTLY_REVIEW_LIMIT = 10


def _anomalous_segments(
    db: Session, anomaly: ScrapeAnomaly
) -> list[dict[str, object]]:
    """Which Source Segments tripped a threshold, and why.

    ``ScraperRun.details["anomalousSegments"]`` carries only the keys, which
    names the segment but not the reason an administrator is being asked to
    confirm. The per-segment reasons live on the run's segment results, so read
    them there rather than asking someone to decide on a bare key.
    """
    return [
        {"key": result.segment_key, "reasons": result.anomaly_reasons or []}
        for result in db.scalars(
            select(ScrapeSegmentResult)
            .where(
                ScrapeSegmentResult.scraper_run_id == anomaly.scraper_run_id,
                ScrapeSegmentResult.anomalous.is_(True),
            )
            .order_by(ScrapeSegmentResult.segment_key)
        )
    ]


def _anomaly_dict(
    anomaly: ScrapeAnomaly,
    run: ScraperRun | None,
    segments: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "id": str(anomaly.id),
        "status": anomaly.status,
        "scraperRunId": anomaly.scraper_run_id,
        "configurationId": str(anomaly.configuration_id),
        "datasetChecksum": anomaly.dataset_checksum,
        "decisionBy": anomaly.decision_by,
        "decisionReason": anomaly.decision_reason,
        "decidedAt": anomaly.decided_at,
        "createdAt": anomaly.created_at,
        "runStatus": run.status if run is not None else None,
        # The evidence the confirm/deny decision rests on. Without it an
        # administrator is asked to judge output they cannot see.
        "anomalousSegments": segments,
        "decidable": anomaly.status == PENDING,
    }


def recommendation_dict(
    item: SourceRecommendation, *, decidable: bool = True
) -> dict[str, object]:
    """One Source Recommendation, evidence included.

    ``decidable`` is omitted for the plain list endpoint, which has no action
    surface; the workspace reads it to decide what to offer.
    """
    body = {
        "id": str(item.id),
        "niche": item.niche,
        "segment": item.segment_key,
        "action": item.action,
        "status": item.status,
        "evidence": item.evidence,
        "evidenceChecksum": item.evidence_checksum,
        "configurationVersion": item.configuration_version,
        "decisionBy": item.decision_by,
        "decisionReason": item.decision_reason,
        "decidedAt": item.decided_at,
        "createdAt": item.created_at,
        "resultingConfigurationId": (
            str(item.resulting_configuration_id)
            if item.resulting_configuration_id is not None
            else None
        ),
    }
    if decidable:
        body["decidable"] = item.status == PENDING
    return body


def niche_dict(item: SourceNicheMapping) -> dict[str, object]:
    return {
        "segment": item.segment_key,
        "state": item.state,
        "keyword": item.keyword,
        "niche": item.niche,
        "confirmed": item.confirmed,
        "proposalSource": item.proposal_source,
        "evidence": item.proposed_evidence,
        "confirmedBy": item.confirmed_by,
        "confirmedAt": item.confirmed_at,
    }


def _review_dict(review: NightlyReview) -> dict[str, object]:
    return {
        "id": str(review.id),
        "scraperRunId": review.scraper_run_id,
        "reviewDate": review.review_date,
        "status": review.status,
        "summary": review.summary,
        "telegramDeliveryState": review.telegram_delivery_state,
        "telegramMessageId": review.telegram_message_id,
        "telegramDeliveryError": review.telegram_delivery_error,
        "createdAt": review.created_at,
        # Only an unknown delivery is reconcilable; the endpoint refuses the
        # rest with a 409, so the workspace must not offer it for them.
        "reconcilable": review.telegram_delivery_state == "unknown",
    }


def nightly_reviews_needing_attention(
    db: Session,
) -> list[dict[str, object]]:
    """Every Nightly Review with a real operator recovery step.

    The Acquisition workspace limits history for scanning, but the Operations
    count cannot silently cap pending work at that history limit.
    """
    reviews = db.scalars(
        select(NightlyReview).order_by(
            NightlyReview.created_at.desc(),
            NightlyReview.id,
        )
    )
    attention = []
    for review in reviews:
        failures = (
            review.summary.get("failures")
            if isinstance(review.summary, dict)
            else None
        )
        if (
            review.status != "complete"
            or review.telegram_delivery_state == "unknown"
            or (isinstance(failures, list) and failures)
        ):
            attention.append(_review_dict(review))
    return attention


def workspace(db: Session) -> dict[str, object]:
    """Everything the Acquisition workspace opens on."""
    anomalies = []
    for anomaly in db.scalars(
        select(ScrapeAnomaly).order_by(ScrapeAnomaly.created_at.desc())
    ):
        run = db.get(ScraperRun, anomaly.scraper_run_id)
        anomalies.append(
            _anomaly_dict(anomaly, run, _anomalous_segments(db, anomaly))
        )

    configurations = [
        {
            "id": str(item.id),
            "version": item.version,
            "checksum": item.checksum,
            "status": item.status,
            "reason": item.reason,
            "createdAt": item.created_at,
            "scheduledAt": item.scheduled_at,
            "activatedAt": item.activated_at,
            "basedOnConfigurationId": (
                str(item.based_on_configuration_id)
                if item.based_on_configuration_id is not None
                else None
            ),
            "segmentCount": len(item.segments),
            "anomalyThresholds": item.anomaly_thresholds,
        }
        for item in db.scalars(
            select(ScraperConfiguration).order_by(
                ScraperConfiguration.version.desc()
            )
        )
    ]

    return {
        "nightlyReviews": [
            _review_dict(review)
            for review in db.scalars(
                select(NightlyReview)
                .order_by(NightlyReview.created_at.desc())
                .limit(NIGHTLY_REVIEW_LIMIT)
            )
        ],
        "scrapeAnomalies": anomalies,
        "sourceRecommendations": [
            recommendation_dict(item)
            for item in db.scalars(
                select(SourceRecommendation).order_by(
                    SourceRecommendation.created_at.desc(),
                    SourceRecommendation.id,
                )
            )
        ],
        "nicheMappings": [
            niche_dict(item)
            for item in db.scalars(
                select(SourceNicheMapping).order_by(
                    SourceNicheMapping.segment_key
                )
            )
        ],
        "scraperConfigurations": configurations,
    }
