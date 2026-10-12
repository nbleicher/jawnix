"""Jawnix-owned automatic keyword rollover.

The acquisition host reports campaign drain through the typed control API;
Jawnix owns the decision, the OpenRouter call, and the audited activation.
The control service never holds a model credential (Stage B ownership rule).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from .activity import record_activity
from .config import Settings
from .keyword_generation import (
    KEYWORD_CANDIDATE_BATCH_SIZE,
    KEYWORD_INDUSTRY_FOCI,
    GenerationErrorCode,
    KeywordGenerationError,
    build_generation_provider,
    exclusion_metrics,
    generation_error,
    keyword_history_terms,
    try_generation_lock,
    validated_keyword_terms,
)
from .keyword_history import observe_keyword_history
from .models import KeywordCandidatePool
from .scraper_keywords import (
    KeywordRolloverEventRequest,
    KeywordSaveRequest,
    KeywordWorkspace,
)
from .scraper_operations import (
    HTTPScraperOperations,
    ScraperOperations,
    ScraperOperationsError,
)

ROLLOVER_ACTOR = "system:keyword-rollover"
ROLLOVER_TARGET = "scraper_keyword_rollover"
ROLLOVER_FAILURE_COOLDOWN = timedelta(minutes=15)
ACTIVATION_MESSAGE = "Activated 25 automatically generated keywords"
POOL_TARGET = 50


def _record_failure(
    session: Session,
    *,
    reason: str,
    details: dict,
    now: datetime,
) -> None:
    entry = record_activity(
        session,
        action="scraper_keyword_rollover_failed",
        target_type=ROLLOVER_TARGET,
        target_id="primary",
        actor_id=ROLLOVER_ACTOR,
        reason=reason,
        details=details,
    )
    # Cooldown reads created_at against the same clock the runner was given.
    entry.created_at = now


async def _record_error_event(
    operations: ScraperOperations,
    message: str,
) -> None:
    try:
        await operations.record_keyword_rollover_event(
            KeywordRolloverEventRequest(status="error", message=message[:300])
        )
    except ScraperOperationsError:
        # The Jawnix audit entry above is the durable record; a transient
        # control-service failure must not mask the original error.
        pass


async def run_automatic_keyword_rollover(
    session: Session,
    settings: Settings,
    *,
    operations: ScraperOperations | None = None,
    provider=None,
    now: datetime | None = None,
) -> dict:
    """Fill ahead, retain partial work, and activate a complete unused batch."""

    now = now or datetime.now(timezone.utc)
    operations = operations or HTTPScraperOperations(settings)
    workspace = await operations.list_keywords()
    rollover = workspace.rollover
    if not rollover.enabled:
        return {"outcome": "idle", "state": rollover.state}
    if not try_generation_lock(session):
        return {"outcome": "locked"}
    pool = session.get(KeywordCandidatePool, 1, with_for_update=True)
    if pool is None:
        pool = KeywordCandidatePool(id=1, candidates=[], focus_index=0)
        session.add(pool)
        session.flush()
    winners = await operations.keyword_winners()
    history = keyword_history_terms(session)
    observe_keyword_history(
        session,
        workspace.current,
        origin="active_list",
        observed_at=now,
    )
    observe_keyword_history(
        session,
        (winner.keyword for winner in winners),
        origin="winner",
        observed_at=now,
    )
    exclusions, counts = exclusion_metrics(
        active=workspace.current,
        winners=(winner.keyword for winner in winners),
        history=history,
    )
    # Active terms, imported history, and newly selected terms all remain
    # excluded. Another operator may have used a candidate since it was saved.
    valid = validated_keyword_terms(
        (item["term"] for item in pool.candidates),
        exclusions,
        limit=POOL_TARGET,
    )
    by_term = {item["term"]: item for item in reversed(pool.candidates)}
    pool.candidates = [by_term[term] for term in valid]
    if rollover.state == "ready" and len(pool.candidates) >= 25:
        return await _activate_pool(session, operations, workspace, pool, counts, now)
    if len(pool.candidates) >= POOL_TARGET:
        return {"outcome": "idle", "state": rollover.state}
    refill_at = pool.next_refill_at
    if refill_at and refill_at.tzinfo is None:
        refill_at = refill_at.replace(tzinfo=timezone.utc)
    if refill_at and refill_at > now:
        return {"outcome": "cooldown"}

    provider = provider or build_generation_provider(settings)
    if not provider.available:
        message = "OpenRouter is not configured for automatic rollover"
        pool.next_refill_at = now + ROLLOVER_FAILURE_COOLDOWN
        _record_failure(
            session,
            reason="Automatic keyword rollover requires Jawnix generation",
            details={"outcome": "not_configured"},
            now=now,
        )
        await _record_error_event(operations, message)
        return {"outcome": "not_configured"}
    focus = KEYWORD_INDUSTRY_FOCI[pool.focus_index % len(KEYWORD_INDUSTRY_FOCI)]
    pool.focus_index += 1
    pending = [item["term"] for item in pool.candidates]
    failure = None
    try:
        result = provider.generate_keywords(
            mode="broad",
            excluded_keywords=[*exclusions, *pending],
            count=min(KEYWORD_CANDIDATE_BATCH_SIZE, POOL_TARGET - len(pending)),
            industry_focus=focus,
        )
        candidates, metrics = result.terms, result.candidate_metrics
    except KeywordGenerationError as error:
        failure = error
        candidates, metrics = error.partial_terms, error.metrics
    accepted = validated_keyword_terms(
        candidates,
        [*exclusions, *pending],
        limit=min(KEYWORD_CANDIDATE_BATCH_SIZE, POOL_TARGET - len(pending)),
    )
    candidate_models = metrics.get("termModels", {})
    pool.candidates = [
        *pool.candidates,
        *[
            {
                "term": term,
                "model": candidate_models.get(term, provider.model),
                "focus": focus,
                "createdAt": now.isoformat(),
            }
            for term in accepted
        ],
    ]
    pool.updated_at = now
    account_blocked = failure and failure.code in {
        GenerationErrorCode.INVALID_API_KEY,
        GenerationErrorCode.INSUFFICIENT_CREDIT,
        GenerationErrorCode.RATE_LIMITED,
    }
    pool.next_refill_at = now + (
        timedelta(minutes=1)
        if accepted and not account_blocked
        else ROLLOVER_FAILURE_COOLDOWN
    )
    if accepted:
        record_activity(
            session,
            action="scraper_keyword_pool_refilled",
            target_type=ROLLOVER_TARGET,
            target_id="primary",
            actor_id=ROLLOVER_ACTOR,
            reason="Validated keyword candidates retained for a future batch",
            details={
                "added": len(accepted),
                "available": len(pool.candidates),
                "focus": focus,
                "candidateMetrics": metrics,
                "exclusions": counts,
            },
        )
    else:
        message = (
            failure.message
            if failure
            else generation_error(GenerationErrorCode.INSUFFICIENT_CANDIDATES).message
        )
        _record_failure(
            session,
            reason="Automatic keyword rollover generation failed",
            details={
                "outcome": "generation_failed",
                "message": message,
                "exclusions": counts,
                "candidateMetrics": metrics,
            },
            now=now,
        )
        await _record_error_event(operations, message)
        return {"outcome": "generation_failed", "message": message}
    if rollover.state == "ready" and len(pool.candidates) >= 25:
        return await _activate_pool(session, operations, workspace, pool, counts, now)
    return {
        "outcome": "pooling",
        "available": len(pool.candidates),
        "added": len(accepted),
    }


async def _activate_pool(
    session: Session,
    operations: ScraperOperations,
    workspace: KeywordWorkspace,
    pool: KeywordCandidatePool,
    counts: dict,
    now: datetime,
) -> dict:
    # A model request can take minutes. Recheck readiness and permanent history
    # after it returns, before submitting the version-checked activation.
    try:
        latest = await operations.list_keywords()
        winners = await operations.keyword_winners()
    except ScraperOperationsError as error:
        _record_failure(
            session,
            reason="Automatic keyword rollover could not revalidate candidates",
            details={
                "outcome": "revalidation_failed",
                "upstreamStatus": error.status_code,
            },
            now=now,
        )
        await _record_error_event(
            operations, "Automatic rollover could not revalidate the prepared keywords"
        )
        return {"outcome": "revalidation_failed"}
    if latest.version != workspace.version:
        return {"outcome": "conflict"}
    if not latest.rollover.enabled or latest.rollover.state != "ready":
        return {"outcome": "pooling", "available": len(pool.candidates), "added": 0}
    exclusions, counts = exclusion_metrics(
        active=latest.current,
        winners=(winner.keyword for winner in winners),
        history=keyword_history_terms(session),
    )
    valid = validated_keyword_terms(
        (item["term"] for item in pool.candidates), exclusions, limit=POOL_TARGET
    )
    by_term = {item["term"]: item for item in reversed(pool.candidates)}
    pool.candidates = [by_term[term] for term in valid]
    if len(pool.candidates) < 25:
        return {"outcome": "pooling", "available": len(pool.candidates), "added": 0}
    selected = pool.candidates[:25]
    terms = [item["term"] for item in selected]

    try:
        saved = await operations.save_keywords(
            KeywordSaveRequest(
                text="\n".join(terms),
                expected_version=workspace.version,
                review_token="automatic-rollover",
                enqueue=True,
            )
        )
    except ScraperOperationsError as error:
        if error.status_code == 409:
            # Keep the candidates and revalidate against the changed list
            # on the next check.
            return {"outcome": "conflict"}
        _record_failure(
            session,
            reason="Automatic keyword rollover could not save keywords",
            details={
                "outcome": "save_failed",
                "upstreamStatus": error.status_code,
            },
            now=now,
        )
        await _record_error_event(
            operations,
            "Automatic rollover could not activate the generated keywords",
        )
        return {"outcome": "save_failed"}

    observe_keyword_history(
        session,
        saved.current,
        origin="active_list",
        observed_at=now,
    )
    event_recorded = True
    try:
        await operations.record_keyword_rollover_event(
            KeywordRolloverEventRequest(
                status="generated",
                previous_keywords=list(workspace.current),
                next_keywords=list(saved.current),
                message=ACTIVATION_MESSAGE,
            )
        )
    except ScraperOperationsError:
        # Activation has already succeeded. Commit its local history and pool
        # removal even when the control service's event endpoint is unavailable.
        event_recorded = False
    record_activity(
        session,
        action="scraper_keyword_rollover_completed",
        target_type=ROLLOVER_TARGET,
        target_id="primary",
        actor_id=ROLLOVER_ACTOR,
        reason="Automatic keyword rollover activated the next batch",
        details={
            "models": sorted({item["model"] for item in selected}),
            "previousCount": len(workspace.current),
            "activatedCount": len(saved.current),
            "enqueued": saved.enqueued,
            "eventRecorded": event_recorded,
            "exclusions": counts,
        },
    )
    pool.candidates = pool.candidates[25:]
    return {"outcome": "generated", "activated": len(saved.current)}
