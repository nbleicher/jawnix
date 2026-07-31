from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .activity import record_activity
from .config import Settings
from .jobs import enqueue_job
from .keyword_generation import (
    KeywordGenerationError,
    build_generation_provider,
    keyword_history_terms,
    try_generation_lock,
)
from .models import (
    DatasetPublication,
    DistributionEvent,
    InventoryConflict,
    InventorySyncAttempt,
    Lead,
    LeadRequest,
    NightlyReview,
    ScraperConfiguration,
    ScraperRun,
    SourceSegment,
    SourceRecommendation,
    SourceNicheMapping,
)
from .optimization import analyze_nightly_performance


BASELINE_ACTOR_ID = uuid.uuid5(
    uuid.NAMESPACE_URL,
    "https://jawnix.com/actors/scraper-baseline",
)
SOURCE_SEGMENT_STATUSES = {"active", "reduced", "paused"}


def _external_source_segment_contract(payload: dict) -> tuple[int, str, list[dict]]:
    version = int(payload["version"])
    checksum = str(payload["checksum"])
    if version < 1 or len(checksum) != 64:
        raise ValueError("Invalid Scraper Source Segment contract.")
    rows = []
    for raw in payload["segments"]:
        keyword = " ".join(str(raw["keyword"]).strip().split()).casefold()
        state = str(raw["state"]).strip().upper()
        identity = f"{state}::{keyword}"
        status = str(raw.get("status") or "active").lower()
        cadence = float(raw["cadenceMultiplier"])
        expected_cadence = {
            "active": 1.0,
            "reduced": 0.5,
            "paused": 0.0,
        }.get(status)
        if (
            not keyword
            or len(state) != 2
            or str(raw["id"]) != identity
            or status not in SOURCE_SEGMENT_STATUSES
            or cadence != expected_cadence
        ):
            raise ValueError("Invalid Scraper Source Segment.")
        rows.append(
            {
                "id": identity,
                "keyword": keyword,
                "state": state,
                "niche": str(raw.get("niche") or "").strip(),
                "niche_confirmed": bool(
                    raw.get("nicheConfirmed", False)
                ),
                "status": status,
                "cadence_multiplier": cadence,
                "version": version,
                "seed_segment_id": (
                    str(raw["seedSegmentId"])
                    if raw.get("seedSegmentId")
                    else None
                ),
            }
        )
    if not rows or [item["id"] for item in rows] != sorted(
        item["id"] for item in rows
    ):
        raise ValueError(
            "Scraper Source Segments must be non-empty and sorted."
        )
    calculated = hashlib.sha256(
        json.dumps(
            {"version": version, "segments": rows},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    if calculated != checksum:
        raise ValueError("Scraper Source Segment checksum does not match.")
    return version, checksum, rows


def sync_scraper_configuration_baseline(
    session: Session,
    settings: Settings,
    *,
    as_of: datetime | None = None,
) -> ScraperConfiguration | None:
    existing = session.scalar(
        select(ScraperConfiguration)
        .order_by(ScraperConfiguration.version.desc())
        .limit(1)
    )
    if existing is not None or not settings.scraper_ops_url:
        return existing
    response = httpx.get(
        settings.scraper_ops_url.rstrip("/") + "/api/source-segments",
        auth=(
            settings.scraper_ops_user,
            settings.scraper_ops_password,
        ),
        timeout=settings.scraper_ops_timeout_seconds,
    )
    response.raise_for_status()
    version, checksum, rows = _external_source_segment_contract(
        response.json()
    )
    imported = ScraperConfiguration(
        version=version,
        checksum=checksum,
        status="active",
        anomaly_thresholds={},
        created_by=BASELINE_ACTOR_ID,
        reason=(
            "Imported from the authoritative Scraper Source Segment "
            "configuration."
        ),
        activated_at=as_of or datetime.now(timezone.utc),
        segments=[
            SourceSegment(
                key=item["id"],
                niche=item["niche"],
                query=item["keyword"],
                geography=item["state"],
                parameters={
                    "state": item["state"],
                    "status": item["status"],
                    "cadence_multiplier": item["cadence_multiplier"],
                    "niche_confirmed": item["niche_confirmed"],
                    **(
                        {"seed_segment_key": item["seed_segment_id"]}
                        if item["seed_segment_id"]
                        else {}
                    ),
                },
            )
            for item in rows
        ],
    )
    session.add(imported)
    session.flush()
    record_activity(
        session,
        action="scraper_configuration_baseline_imported",
        target_type="scraper_configuration",
        target_id=imported.id,
        actor_id="system:nightly-scheduler",
        reason=(
            "Reconciled the existing authoritative Scraper Source "
            "Segment baseline without changing acquisition."
        ),
        details={
            "before": None,
            "after": {
                "version": version,
                "status": "active",
                "segmentCount": len(rows),
            },
            "contractChecksum": checksum,
            "acquisitionChanged": False,
        },
    )
    session.flush()
    return imported


def _scale_segment_payload(
    configuration: ScraperConfiguration,
) -> dict:
    rows = []
    for segment in sorted(
        configuration.segments,
        key=lambda item: item.key,
    ):
        state, separator, keyword = segment.key.partition("::")
        parameters = segment.parameters or {}
        if not separator:
            state = str(parameters.get("state") or segment.geography)
            keyword = segment.query
        status = str(parameters.get("status") or "active")
        cadence = float(
            parameters.get(
                "cadence_multiplier",
                0.5 if status == "reduced" else (
                    0.0 if status == "paused" else 1.0
                ),
            )
        )
        rows.append(
            {
                "id": segment.key,
                "keyword": " ".join(keyword.strip().split()).casefold(),
                "state": state.strip().upper(),
                "niche": segment.niche,
                "niche_confirmed": bool(
                    parameters.get("niche_confirmed", False)
                ),
                "status": status,
                "cadence_multiplier": cadence,
                "version": configuration.version,
                "seed_segment_id": parameters.get("seed_segment_key"),
            }
        )
    contract = {
        "version": configuration.version,
        "segments": rows,
    }
    return {
        "version": configuration.version,
        "checksum": hashlib.sha256(
            json.dumps(
                contract,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest(),
        "segments": [
            {
                key: value
                for key, value in row.items()
                if key != "version" and value is not None
            }
            for row in rows
        ],
    }


def activate_scheduled_scraper_configuration(
    session: Session,
    settings: Settings,
    *,
    as_of: datetime | None = None,
) -> ScraperConfiguration | None:
    as_of = as_of or datetime.now(timezone.utc)
    scheduled = session.scalar(
        select(ScraperConfiguration)
        .where(
            ScraperConfiguration.status == "scheduled",
            ScraperConfiguration.scheduled_at <= as_of,
        )
        .order_by(ScraperConfiguration.version)
        .limit(1)
        .with_for_update()
    )
    if scheduled is None:
        return None
    if not settings.scraper_ops_url:
        raise RuntimeError(
            "JAWNIX_SCRAPER_OPS_URL is required to activate a "
            "scheduled Source Segment configuration."
        )
    payload = _scale_segment_payload(scheduled)
    response = httpx.post(
        settings.scraper_ops_url.rstrip("/")
        + "/api/source-segments/activate",
        auth=(
            settings.scraper_ops_user,
            settings.scraper_ops_password,
        ),
        json=payload,
        timeout=settings.scraper_ops_timeout_seconds,
    )
    response.raise_for_status()
    accepted = response.json()
    if (
        int(accepted.get("version") or 0) != scheduled.version
        or accepted.get("checksum") != payload["checksum"]
    ):
        raise RuntimeError(
            "Scraper accepted a different Source Segment revision."
        )
    for active in session.scalars(
        select(ScraperConfiguration)
        .where(ScraperConfiguration.status == "active")
        .with_for_update()
    ):
        active.status = "superseded"
    scheduled.status = "active"
    scheduled.activated_at = as_of
    record_activity(
        session,
        action="scraper_configuration_activated",
        target_type="scraper_configuration",
        target_id=scheduled.id,
        actor_id="system:nightly-scheduler",
        reason="Scheduled explicit Source Segment activation",
        details={
            "before": {"status": "scheduled"},
            "after": {"status": "active"},
            "version": scheduled.version,
            "contractChecksum": payload["checksum"],
            "scraperVersion": accepted["version"],
        },
    )
    session.flush()
    return scheduled


def propose_niche_mappings(
    session: Session,
    settings: Settings | None = None,
) -> int:
    existing = {
        item.segment_key: item
        for item in session.scalars(select(SourceNicheMapping))
    }
    candidates: dict[str, dict] = {}
    configuration = session.scalar(
        select(ScraperConfiguration)
        .where(ScraperConfiguration.status == "active")
        .order_by(ScraperConfiguration.version.desc())
        .limit(1)
    )
    if configuration is not None:
        for segment in configuration.segments:
            current = existing.get(segment.key)
            if current is not None and (
                current.confirmed or current.niche.strip()
            ):
                continue
            parameters = segment.parameters or {}
            state, separator, keyword = segment.key.partition("::")
            if not separator:
                state = str(
                    parameters.get("state") or segment.geography
                )
                keyword = segment.query
            candidates[segment.key] = {
                "segment_key": segment.key,
                "state": state.upper(),
                "keyword": keyword.strip().casefold(),
                "niche": segment.niche.strip(),
                "confirmed": bool(
                    parameters.get("niche_confirmed", False)
                ),
                "proposal_source": "scraper_configuration",
                "evidence": {
                    "configurationId": str(configuration.id),
                    "configurationVersion": configuration.version,
                    "acquisitionChanged": False,
                },
            }
    for event in session.scalars(
        select(DistributionEvent)
        .where(
            DistributionEvent.source_kind == "google_maps",
            DistributionEvent.source_segment_key != "",
        )
        .order_by(DistributionEvent.id)
    ):
        current = existing.get(event.source_segment_key)
        if current is not None and (
            current.confirmed or current.niche.strip()
        ):
            continue
        state, separator, keyword = (
            event.source_segment_key.partition("::")
        )
        candidates.setdefault(
            event.source_segment_key,
            {
                "segment_key": event.source_segment_key,
                "state": (
                    state.upper() if separator else event.state.upper()
                ),
                "keyword": (
                    keyword.strip().casefold()
                    if separator
                    else event.source_segment_key.strip().casefold()
                ),
                "niche": event.source_niche.strip(),
                "confirmed": False,
                "proposal_source": "historical_provenance",
                "evidence": {
                    "distributionEventId": event.id,
                    "sourceNiche": event.source_niche,
                    "acquisitionChanged": False,
                },
            },
        )
    ai_proposals: dict[str, str] = {}
    provider = build_generation_provider(settings) if settings else None
    if (
        candidates
        and provider
        and provider.available
        and try_generation_lock(session)
    ):
        inputs = []
        for item in candidates.values():
            inputs.append(
                {
                    "id": item["segment_key"],
                    "keyword": item["keyword"],
                    "state": item["state"],
                }
            )
        try:
            proposals = provider.propose_niches(inputs)
            ai_proposals.update(
                {
                    str(item["id"]): str(item["niche"]).strip()
                    for item in proposals
                }
            )
        except (KeywordGenerationError, KeyError, TypeError, ValueError):
            pass
    proposed = 0
    for item in candidates.values():
        segment_key = item["segment_key"]
        proposed_niche = ai_proposals.get(segment_key)
        current = existing.get(segment_key)
        if current is not None:
            if proposed_niche and not current.confirmed:
                current.niche = proposed_niche
                current.proposal_source = "ai_openrouter"
                current.proposed_evidence = {
                    **(current.proposed_evidence or {}),
                    "proposalRefreshed": True,
                    "acquisitionChanged": False,
                }
                proposed += 1
            continue
        session.add(
            SourceNicheMapping(
                segment_key=segment_key,
                state=item["state"],
                keyword=item["keyword"],
                niche=proposed_niche or item["niche"],
                confirmed=bool(item["confirmed"]),
                proposal_source=(
                    "ai_openrouter"
                    if proposed_niche
                    else item["proposal_source"]
                ),
                proposed_evidence=item["evidence"],
                confirmed_by=(
                    "scraper_configuration"
                    if item["confirmed"]
                    else ""
                ),
                confirmed_at=(
                    datetime.now(timezone.utc)
                    if item["confirmed"]
                    else None
                ),
            )
        )
        existing[segment_key] = None
        proposed += 1
    session.flush()
    return proposed


def _scraper_health(settings: Settings) -> dict:
    if not settings.scraper_ops_url:
        return {
            "status": "not_configured",
            "nativeAnalyticsAvailable": True,
        }
    try:
        response = httpx.get(
            settings.scraper_ops_url.rstrip("/") + "/",
            auth=(
                settings.scraper_ops_user,
                settings.scraper_ops_password,
            ),
            timeout=settings.scraper_ops_timeout_seconds,
        )
        return {
            "status": (
                "reachable"
                if response.status_code < 500
                else "upstream_error"
            ),
            "httpStatus": response.status_code,
            "nativeAnalyticsAvailable": True,
        }
    except httpx.HTTPError as exc:
        return {
            "status": "unreachable",
            "error": str(exc)[:500],
            "nativeAnalyticsAvailable": True,
        }


def _external_publication(
    settings: Settings,
    *,
    review_date,
) -> dict | None:
    if not settings.scraper_ops_url:
        return None
    try:
        response = httpx.get(
            settings.scraper_ops_url.rstrip("/")
            + "/api/source-segments/publication",
            auth=(
                settings.scraper_ops_user,
                settings.scraper_ops_password,
            ),
            timeout=settings.scraper_ops_timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    if (
        data.get("status") != "committed"
        or data.get("publicationDate") != review_date.isoformat()
    ):
        return None
    return data


def _attach_adjacent_keyword_evidence(
    session: Session,
    settings: Settings,
    recommendations: list[SourceRecommendation],
) -> list[dict]:
    failures: list[dict] = []
    provider = build_generation_provider(settings)
    if not provider.available:
        return failures
    if not try_generation_lock(session):
        return [
            {
                "kind": "adjacent_keyword_proposal_failed",
                "segment": recommendation.segment_key,
                "message": "Another keyword generation is already running",
            }
            for recommendation in recommendations
            if recommendation.action == "expand"
        ]
    excluded = [
        item.keyword
        for item in session.scalars(select(SourceNicheMapping))
    ]
    excluded.extend(keyword_history_terms(session))
    for recommendation in recommendations:
        if recommendation.action != "expand":
            continue
        _, _, seed_keyword = recommendation.segment_key.partition("::")
        try:
            result = provider.generate_keywords(
                mode="adjacent",
                excluded_keywords=excluded,
                seed_keyword=seed_keyword,
                count=3,
            )
            adjacent = result.terms
            if not adjacent:
                raise ValueError("No adjacent keywords were proposed.")
        except (
            KeywordGenerationError,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            failures.append(
                {
                    "kind": "adjacent_keyword_proposal_failed",
                    "segment": recommendation.segment_key,
                    "message": str(exc)[:500],
                }
            )
            continue
        evidence = {
            **(recommendation.evidence or {}),
            "state": recommendation.segment_key.partition("::")[0],
            "adjacentKeywords": adjacent,
        }
        recommendation.evidence = evidence
        recommendation.evidence_checksum = hashlib.sha256(
            json.dumps(
                evidence,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode()
        ).hexdigest()
        excluded.extend(adjacent)
    session.flush()
    return failures


def run_scheduled_nightly_review(
    session: Session,
    settings: Settings,
    *,
    as_of: datetime | None = None,
) -> NightlyReview:
    as_of = as_of or datetime.now(timezone.utc)
    review_date = as_of.date()
    review = session.scalar(
        select(NightlyReview)
        .where(NightlyReview.review_date == review_date)
        .with_for_update()
    )
    scheduled_for = as_of.replace(
        hour=settings.nightly_review_hour_utc,
        minute=0,
        second=0,
        microsecond=0,
    )
    if review is None:
        review = NightlyReview(
            review_date=review_date,
            scheduled_for=scheduled_for,
            status="waiting_publication",
            summary={},
        )
        session.add(review)
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            review = session.scalar(
                select(NightlyReview).where(
                    NightlyReview.review_date == review_date
                )
            )
            if review is None:
                raise
    elif review.status == "complete":
        has_configuration = session.scalar(
            select(func.count(ScraperConfiguration.id))
        )
        if has_configuration:
            return review
    else:
        review.attempt_count += 1

    day_start = datetime.combine(
        review_date,
        datetime.min.time(),
        tzinfo=timezone.utc,
    )
    publication = session.scalar(
        select(DatasetPublication)
        .where(
            DatasetPublication.committed_at >= day_start,
            DatasetPublication.committed_at <= as_of,
            DatasetPublication.sync_status == "complete",
        )
        .order_by(DatasetPublication.committed_at.desc())
        .limit(1)
    )
    external_publication = (
        _external_publication(
            settings,
            review_date=review_date,
        )
        if publication is None
        else None
    )
    if publication is None and external_publication is None:
        review.status = "waiting_publication"
        review.next_retry_at = as_of + timedelta(
            minutes=settings.nightly_review_retry_minutes
        )
        review.summary = {
            **review.summary,
            "publication": {"status": "delayed"},
            "scraperOperations": _scraper_health(settings),
            "failures": [
                {
                    "kind": "publication_delayed",
                    "message": (
                        "No committed, synchronized publication is "
                        "available for this UTC day."
                    ),
                }
            ],
        }
        session.flush()
        return review

    if publication is not None:
        review.scraper_run_id = publication.scraper_run_id
    review.next_retry_at = None
    sync_scraper_configuration_baseline(
        session,
        settings,
        as_of=as_of,
    )
    propose_niche_mappings(session, settings)
    snapshots = analyze_nightly_performance(
        session,
        review,
        as_of=as_of,
    )
    created_recommendations = list(
        session.scalars(
            select(SourceRecommendation)
            .where(SourceRecommendation.nightly_review_id == review.id)
            .order_by(SourceRecommendation.created_at)
        )
    )
    review_failures = _attach_adjacent_keyword_evidence(
        session,
        settings,
        created_recommendations,
    )
    pending_recommendations = sum(
        item.action_state in {"expand", "reduce", "pause"}
        for item in snapshots
    )
    recommendations = [
        {
            "id": str(item.id),
            "niche": item.niche,
            "segment": item.segment_key,
            "action": item.action,
            "status": item.status,
            "evidenceChecksum": item.evidence_checksum,
            "configurationVersion": item.configuration_version,
        }
        for item in created_recommendations
    ]
    review.status = "complete"
    configuration = session.scalar(
        select(ScraperConfiguration)
        .order_by(ScraperConfiguration.version.desc())
        .limit(1)
    )
    scraper_run = (
        session.get(ScraperRun, publication.scraper_run_id)
        if publication is not None
        else None
    )
    sync_attempt = (
        session.scalar(
            select(InventorySyncAttempt)
            .where(
                InventorySyncAttempt.dataset_publication_id
                == publication.id
            )
            .order_by(InventorySyncAttempt.attempt_number.desc())
            .limit(1)
        )
        if publication is not None
        else None
    )
    waiting_requests = list(
        session.scalars(
            select(LeadRequest)
            .where(LeadRequest.status == "waiting_inventory")
            .order_by(LeadRequest.created_at)
        )
    )
    inventory_conflicts = list(
        session.scalars(
            select(InventoryConflict)
            .where(InventoryConflict.status == "pending")
            .order_by(InventoryConflict.created_at)
        )
    )
    review.summary = {
        **review.summary,
        "configuration": {
            "version": (
                configuration.version if configuration is not None else None
            ),
            "status": (
                configuration.status if configuration is not None else "none"
            ),
        },
        "run": {
            "id": scraper_run.id if scraper_run is not None else None,
            "status": (
                scraper_run.status
                if scraper_run is not None
                else "external_publication_verified"
            ),
        },
        "dataset": {
            "version": (
                publication.version if publication is not None else None
            ),
            "syncStatus": (
                publication.sync_status
                if publication is not None
                else "external_committed"
            ),
            "syncAttemptStatus": (
                sync_attempt.status if sync_attempt is not None else None
            ),
        },
        "segments": [
            {
                "key": item.segment_key,
                "state": item.state,
                "keyword": item.keyword,
                "anomalous": False,
                "confidence": item.eligibility,
                "action": item.action_state,
            }
            for item in snapshots
        ],
        "inventory": {
            "total": int(
                session.scalar(select(func.count(Lead.id))) or 0
            ),
            "eligible": int(
                session.scalar(
                    select(func.count(Lead.id)).where(
                        Lead.suppressed.is_(False)
                    )
                )
                or 0
            ),
        },
        "waitingRequests": [
            {
                "id": str(item.id),
                "leadCount": item.lead_count,
                "status": item.status,
            }
            for item in waiting_requests
        ],
        "inventoryConflicts": [
            {
                "id": str(item.id),
                "olderRequestId": str(item.older_request_id),
                "newerRequestId": str(item.newer_request_id),
                "status": item.status,
            }
            for item in inventory_conflicts
        ],
        "publication": (
            {
                "status": "committed",
                "version": publication.version,
                "checksum": publication.checksum,
                "committedAt": publication.committed_at.isoformat(),
                "source": "jawnix_dataset_publication",
            }
            if publication is not None
            else {
                **external_publication,
                "source": "scraper_operations",
            }
        ),
        "performance": {
            "snapshotCount": len(snapshots),
            "actionableCount": pending_recommendations,
        },
        "recommendations": recommendations,
        "scraperOperations": _scraper_health(settings),
        "failures": review_failures,
    }
    if review.telegram_delivery_state == "pending":
        enqueue_job(
            session,
            "notify_nightly_review",
            payload={"review_id": str(review.id)},
        )
    session.flush()
    return review
