"""Jawnix-owned automatic keyword rollover.

The acquisition host reports campaign drain through the typed control API;
Jawnix owns the decision, the OpenRouter call, and the audited activation.
The control service never holds a model credential (Stage B ownership rule).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .activity import record_activity
from .config import Settings
from .keyword_generation import (
    GenerationErrorCode,
    KeywordGenerationError,
    build_generation_provider,
    exclusion_metrics,
    generation_error,
    keyword_history_terms,
    try_generation_lock,
)
from .keyword_history import observe_keyword_history
from .models import AuditEntry
from .scraper_keywords import (
    KeywordRolloverEventRequest,
    KeywordSaveRequest,
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


def _failure_in_cooldown(session: Session, now: datetime) -> bool:
    cutoff = now - ROLLOVER_FAILURE_COOLDOWN
    return (
        session.scalar(
            select(AuditEntry.id)
            .where(
                AuditEntry.action == "scraper_keyword_rollover_failed",
                AuditEntry.actor_user_id == ROLLOVER_ACTOR,
                AuditEntry.created_at >= cutoff,
            )
            .limit(1)
        )
        is not None
    )


def _record_failure(
    session: Session,
    *,
    reason: str,
    details: dict,
) -> None:
    record_activity(
        session,
        action="scraper_keyword_rollover_failed",
        target_type=ROLLOVER_TARGET,
        target_id="primary",
        actor_id=ROLLOVER_ACTOR,
        reason=reason,
        details=details,
    )


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
    """Run one rollover check; activate the next batch when it is due.

    Returns a small outcome report: ``{"outcome": ...}`` where the outcome is
    one of ``idle``, ``cooldown``, ``locked``, ``not_configured``,
    ``generation_failed``, ``conflict``, ``save_failed``, or ``generated``.
    """

    now = now or datetime.now(timezone.utc)
    operations = operations or HTTPScraperOperations(settings)
    workspace = await operations.list_keywords()
    rollover = workspace.rollover
    if not rollover.enabled or rollover.state != "ready":
        return {"outcome": "idle", "state": rollover.state}
    if _failure_in_cooldown(session, now):
        return {"outcome": "cooldown"}
    if not try_generation_lock(session):
        return {"outcome": "locked"}

    provider = provider or build_generation_provider(settings)
    if not provider.available:
        message = "OpenRouter is not configured for automatic rollover"
        _record_failure(
            session,
            reason="Automatic keyword rollover requires Jawnix generation",
            details={"outcome": "not_configured"},
        )
        await _record_error_event(operations, message)
        return {"outcome": "not_configured"}

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
    try:
        result = provider.generate_keywords(
            mode="broad",
            excluded_keywords=exclusions,
        )
        if len(result.terms) != 25:
            raise generation_error(
                GenerationErrorCode.INSUFFICIENT_CANDIDATES
            )
    except KeywordGenerationError as error:
        _record_failure(
            session,
            reason="Automatic keyword rollover generation failed",
            details={
                "outcome": "generation_failed",
                "message": error.message,
                "exclusions": counts,
            },
        )
        await _record_error_event(operations, error.message)
        return {"outcome": "generation_failed", "message": error.message}

    try:
        saved = await operations.save_keywords(
            KeywordSaveRequest(
                text="\n".join(result.terms),
                expected_version=workspace.version,
                review_token="automatic-rollover",
                enqueue=True,
            )
        )
    except ScraperOperationsError as error:
        if error.status_code == 409:
            # The active list changed while generating; drop the draft and
            # let the next check re-evaluate, exactly like the legacy timer.
            return {"outcome": "conflict"}
        _record_failure(
            session,
            reason="Automatic keyword rollover could not save keywords",
            details={
                "outcome": "save_failed",
                "upstreamStatus": error.status_code,
            },
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
    await operations.record_keyword_rollover_event(
        KeywordRolloverEventRequest(
            status="generated",
            previous_keywords=list(workspace.current),
            next_keywords=list(saved.current),
            message=ACTIVATION_MESSAGE,
        )
    )
    record_activity(
        session,
        action="scraper_keyword_rollover_completed",
        target_type=ROLLOVER_TARGET,
        target_id="primary",
        actor_id=ROLLOVER_ACTOR,
        reason="Automatic keyword rollover activated the next batch",
        details={
            "model": provider.model,
            "previousCount": len(workspace.current),
            "activatedCount": len(saved.current),
            "enqueued": saved.enqueued,
            "exclusions": counts,
            "excludedCandidates": result.excluded_count,
        },
    )
    return {"outcome": "generated", "activated": len(saved.current)}
