"""Jawnix-owned automatic keyword rollover.

The acquisition host reports campaign drain through the typed control API;
Jawnix owns the decision, the provider call, and the audited activation.
The control service never holds a model credential (Stage B ownership rule).
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
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
POOL_TARGET = 150
POOL_REFILL_BELOW = 100
ACTIVATION_COUNT = 25
MIN_ACTIVATION_COUNT = 5
REFILL_INTERVAL = timedelta(seconds=60)
URGENT_REFILL_INTERVAL = timedelta(seconds=15)
_LOCK_NAMESPACE = 0x4A41574E
_GENERATION_RESOURCE = 0x4B574745
_POOL_RESOURCE = 0x4B57504C


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


def _pool_lock(session: Session, *, wait: bool = False) -> bool:
    if session.get_bind().dialect.name != "postgresql":
        return True
    lock = func.pg_advisory_xact_lock if wait else func.pg_try_advisory_xact_lock
    result = session.scalar(select(lock(_LOCK_NAMESPACE, _POOL_RESOURCE)))
    return True if wait else bool(result)


@contextmanager
def _generation_lock(session_factory):
    """One generator across processes, without holding a pool transaction."""
    with session_factory() as session:
        engine = session.get_bind()
    if engine.dialect.name != "postgresql":
        yield True
        return
    with engine.connect() as connection:
        acquired = bool(
            connection.scalar(
                select(func.pg_try_advisory_lock(_LOCK_NAMESPACE, _GENERATION_RESOURCE))
            )
        )
        try:
            connection.commit()
            yield acquired
        finally:
            if acquired:
                try:
                    connection.execute(
                        select(
                            func.pg_advisory_unlock(
                                _LOCK_NAMESPACE, _GENERATION_RESOURCE
                            )
                        )
                    )
                    connection.commit()
                except Exception:
                    # A pooled connection must never retain the session lock.
                    connection.invalidate()
                    raise


def _pool(session: Session) -> KeywordCandidatePool:
    pool = session.get(KeywordCandidatePool, 1, with_for_update=True)
    if pool is None:
        pool = KeywordCandidatePool(id=1, candidates=[], focus_index=0)
        session.add(pool)
        session.flush()
    return pool


def _revalidate_pool(session, pool, workspace, winners, now):
    observe_keyword_history(
        session, workspace.current, origin="active_list", observed_at=now
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
        history=keyword_history_terms(session),
    )
    valid = validated_keyword_terms(
        (item["term"] for item in pool.candidates), exclusions, limit=POOL_TARGET
    )
    by_term = {item["term"]: item for item in reversed(pool.candidates)}
    pool.candidates = [by_term[term] for term in valid]
    return exclusions, counts


def _utc(value):
    return (
        value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value
    )


async def run_automatic_keyword_rollover(
    session: Session,
    settings: Settings,
    *,
    operations: ScraperOperations | None = None,
    now: datetime | None = None,
) -> dict:
    """Activate a prepared batch, independently of model requests and backoff."""
    now = now or datetime.now(timezone.utc)
    operations = operations or HTTPScraperOperations(settings)
    workspace = await operations.list_keywords()
    if not workspace.rollover.enabled:
        return {"outcome": "idle", "state": workspace.rollover.state}
    if not _pool_lock(session):
        return {"outcome": "locked"}
    pool = _pool(session)
    winners = await operations.keyword_winners()
    _, counts = _revalidate_pool(session, pool, workspace, winners, now)
    if len(pool.candidates) < POOL_REFILL_BELOW:
        pool.refilling = True
    if workspace.rollover.state != "ready" or len(pool.candidates) < MIN_ACTIVATION_COUNT:
        return {"outcome": "pooling", "available": len(pool.candidates), "added": 0}
    return await _activate_pool(session, operations, workspace, pool, counts, now)


async def refill_keyword_reserve(
    session_factory,
    settings: Settings,
    *,
    operations: ScraperOperations | None = None,
    provider=None,
    now: datetime | None = None,
) -> dict:
    """Refill in short transactions, releasing the pool during the model call."""
    if settings.keyword_catalogue_enabled:
        return {"outcome": "idle", "reason": "catalogue_primary"}
    started_at = now or datetime.now(timezone.utc)
    operations = operations or HTTPScraperOperations(settings)
    with _generation_lock(session_factory) as acquired:
        if not acquired:
            return {"outcome": "locked"}
        workspace = await operations.list_keywords()
        if not workspace.rollover.enabled:
            return {"outcome": "idle", "state": workspace.rollover.state}
        winners = await operations.keyword_winners()
        with session_factory.begin() as session:
            if not _pool_lock(session):
                return {"outcome": "locked"}
            pool = _pool(session)
            exclusions, counts = _revalidate_pool(
                session, pool, workspace, winners, started_at
            )
            available = len(pool.candidates)
            if available >= POOL_TARGET:
                pool.refilling = False
            elif available < POOL_REFILL_BELOW:
                pool.refilling = True
            if not pool.refilling:
                return {"outcome": "idle", "available": available}
            interval = (
                URGENT_REFILL_INTERVAL
                if available < ACTIVATION_COUNT
                else REFILL_INTERVAL
            )
            retry_at = _utc(pool.next_refill_at)
            last_refill = _utc(pool.last_refill_at)
            if (retry_at and retry_at > started_at) or (
                last_refill and last_refill + interval > started_at
            ):
                return {"outcome": "cooldown"}
            cooldowns = {focus: deadline for focus, deadline in (pool.focus_cooldowns or {}).items()
                if datetime.fromisoformat(deadline) > started_at}
            pool.focus_cooldowns = cooldowns
            focus = None
            for _ in KEYWORD_INDUSTRY_FOCI:
                candidate = KEYWORD_INDUSTRY_FOCI[pool.focus_index % len(KEYWORD_INDUSTRY_FOCI)]
                pool.focus_index += 1
                if candidate not in cooldowns:
                    focus = candidate
                    break
            if focus is None:
                return {"outcome": "cooldown", "available": available}
            pending = [item["term"] for item in pool.candidates]
            requested = min(25 if settings.keyword_generation_provider == "anthropic" else KEYWORD_CANDIDATE_BATCH_SIZE, POOL_TARGET - available)

        # Neither the pool row nor a database transaction is held here.
        provider = provider or build_generation_provider(settings, session_factory=session_factory)
        failure = None
        if not provider.available:
            failure = generation_error(GenerationErrorCode.INVALID_API_KEY)
            candidates, metrics = [], {}
        else:
            try:
                result = provider.generate_keywords(
                    mode="broad",
                    excluded_keywords=[*exclusions, *pending],
                    count=requested,
                    industry_focus=focus,
                )
                candidates, metrics = result.terms, result.candidate_metrics
            except KeywordGenerationError as error:
                failure = error
                candidates, metrics = error.partial_terms, error.metrics
        finished_at = now or datetime.now(timezone.utc)
        # Recheck current state after the model call. If the control service is
        # unavailable, retain candidates against current durable history; the
        # activation process still requires its own successful revalidation.
        refresh_failed = False
        try:
            workspace = await operations.list_keywords()
            winners = await operations.keyword_winners()
        except ScraperOperationsError:
            refresh_failed = True
        with session_factory.begin() as session:
            _pool_lock(session, wait=True)
            pool = _pool(session)
            exclusions, counts = _revalidate_pool(
                session, pool, workspace, winners, finished_at
            )
            room = min(requested, POOL_TARGET - len(pool.candidates))
            if room <= 0:
                pool.refilling = False
                return {"outcome": "idle", "available": len(pool.candidates)}
            accepted = validated_keyword_terms(
                candidates,
                [*exclusions, *(item["term"] for item in pool.candidates)],
                limit=room,
            )
            candidate_models = {
                item["term"]: item["model"] for item in metrics.get("termModels", [])
            }
            pool.candidates = [
                *pool.candidates,
                *[
                    {
                        "term": term,
                        "model": candidate_models.get(term, provider.model),
                        "focus": focus,
                        "createdAt": finished_at.isoformat(),
                    }
                    for term in accepted
                ],
            ]
            pool.updated_at = finished_at
            pool.refilling = len(pool.candidates) < POOL_TARGET
            empty_focus = not accepted and failure and failure.code in {
                GenerationErrorCode.INSUFFICIENT_CANDIDATES,
                GenerationErrorCode.NO_CANDIDATES,
            }
            provider_blocked = failure and failure.code not in {
                GenerationErrorCode.INSUFFICIENT_CANDIDATES,
                GenerationErrorCode.NO_CANDIDATES,
            }
            if empty_focus:
                pool.focus_cooldowns = {**(pool.focus_cooldowns or {}),
                    focus: (finished_at + ROLLOVER_FAILURE_COOLDOWN).isoformat()}
            pool.next_refill_at = (
                finished_at + ROLLOVER_FAILURE_COOLDOWN
                if provider_blocked
                else None
            )
            if accepted:
                pool.last_refill_at = finished_at
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
                        "revalidationFailed": refresh_failed,
                    },
                )
            elif empty_focus:
                record_activity(session, action="scraper_keyword_focus_cooled_down",
                    target_type=ROLLOVER_TARGET, target_id="primary", actor_id=ROLLOVER_ACTOR,
                    reason="No unused candidates in this focus; other focuses remain available",
                    details={"focus": focus, "retryAt": pool.focus_cooldowns[focus],
                        "candidateMetrics": metrics, "available": len(pool.candidates)})
            else:
                message = (
                    f"{'Anthropic' if settings.keyword_generation_provider == 'anthropic' else 'OpenRouter'} is not configured for automatic rollover"
                    if not provider.available
                    else (
                        failure.message
                        if failure
                        else generation_error(
                            GenerationErrorCode.INSUFFICIENT_CANDIDATES
                        ).message
                    )
                )
                _record_failure(
                    session,
                    reason="Automatic keyword rollover generation failed",
                    details={
                        "outcome": (
                            "not_configured"
                            if not provider.available
                            else "generation_failed"
                        ),
                        "message": message,
                        "exclusions": counts,
                        "candidateMetrics": metrics,
                    },
                    now=finished_at,
                )
            available = len(pool.candidates)
        if empty_focus:
            return {"outcome": "pooling", "available": available, "added": 0}
        if not accepted:
            await _record_error_event(operations, message)
            return (
                {"outcome": "not_configured"}
                if not provider.available
                else {"outcome": "generation_failed", "message": message}
            )
        return {"outcome": "pooling", "available": available, "added": len(accepted)}


async def _activate_pool(
    session: Session,
    operations: ScraperOperations,
    workspace: KeywordWorkspace,
    pool: KeywordCandidatePool,
    counts: dict,
    now: datetime,
) -> dict:
    # Recheck readiness and history immediately before version-checked activation.
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
    if len(pool.candidates) < MIN_ACTIVATION_COUNT:
        return {"outcome": "pooling", "available": len(pool.candidates), "added": 0}
    selected = pool.candidates[:ACTIVATION_COUNT]
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
                message=f"Activated {len(selected)} automatically generated keywords",
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
    pool.candidates = pool.candidates[len(selected):]
    if len(pool.candidates) < POOL_REFILL_BELOW:
        pool.refilling = True
    return {"outcome": "generated", "activated": len(saved.current)}
