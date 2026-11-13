from __future__ import annotations

import asyncio
import uuid

from fastapi import Depends, HTTPException, Request
from fastapi.responses import Response
from itsdangerous import BadSignature, SignatureExpired
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

import jawnix.scraper_proxy as _scraper_proxy
from ..activity import record_activity
from ..auth import Principal, require_admin
from ..config import Settings, get_settings
from ..database import get_db
from ..keyword_generation import (
    GenerationErrorCode,
    KeywordGenerationError,
    accept_generation_draft,
    create_generation_draft,
    exclusion_metrics,
    generation_error,
    keyword_history_terms,
    purge_generation_drafts,
    valid_generation_draft,
)
from ..keyword_history import observe_keyword_history
from ..scraper_keywords import (
    KeywordDiff,
    KeywordGenerateRequest,
    KeywordGenerationDraft,
    KeywordRollover,
    KeywordRolloverRequest,
    KeywordSaveRequest,
    KeywordSaveResult,
    KeywordTextRequest,
    KeywordWorkspace,
    diff_keywords,
    keyword_version,
)
from ..scraper_operations import ScraperOperationsError
from .core import (
    KEYWORD_WRITE_LOCK,
    REVIEW_MAX_AGE_SECONDS,
    _generation_provider,
    _keyword_review_serializer,
    _mark_operations_success,
    _native_unavailable,
    _safe_last_success,
    _scraper_operations,
    _state,
    _workspace_now,
    native_router,
)
from .grants import _require_scraper_grant


def _audit_keyword_failure(
    db: Session,
    *,
    principal: Principal,
    action: str,
    reason: str,
    details: dict,
) -> None:
    record_activity(
        db,
        action=action,
        target_type="scraper_keywords",
        target_id="primary",
        actor_id=principal.user_id,
        reason=reason,
        details=details,
    )
    db.commit()


def _public_rollover(rollover: KeywordRollover) -> KeywordRollover:
    if rollover.state != "off":
        return rollover
    return rollover.model_copy(
        update={"posted_jobs": None, "expected_jobs": None}
    )


def _keyword_workspace_unavailable(
    request: Request,
    settings: Settings,
) -> KeywordWorkspace:
    state = _state(request, settings)
    current: list[str] = []
    return KeywordWorkspace(
        service_state="unavailable",
        last_successful_at=_safe_last_success(state),
        current=current,
        version=keyword_version(current),
        ai_enabled=False,
        rollover=KeywordRollover(
            enabled=False,
            state="off",
            label="Unavailable",
            detail="No current rollover status is available.",
            percent_complete=0,
        ),
        winners=[],
    )


@native_router.get("/keywords", response_model=KeywordWorkspace)
async def scraper_keyword_workspace(
    request: Request,
    response: Response,
    principal: Principal = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    """Project every current keyword read without moving ownership to Jawnix."""

    _require_scraper_grant(request, response, principal, settings)
    operations = _scraper_operations(request, settings)
    try:
        workspace, winners = await asyncio.gather(
            operations.list_keywords(),
            operations.keyword_winners(),
        )
    except ScraperOperationsError:
        return _keyword_workspace_unavailable(request, settings)
    _mark_operations_success(request, settings)
    observed_at = _workspace_now(request)
    observe_keyword_history(
        db,
        workspace.current,
        origin="active_list",
        observed_at=observed_at,
    )
    observe_keyword_history(
        db,
        (winner.keyword for winner in winners),
        origin="winner",
        observed_at=observed_at,
    )
    db.commit()
    proxy_state = _state(request, settings)
    from ..performance import keyword_outcome_analytics

    return KeywordWorkspace(
        service_state="connected",
        last_successful_at=_safe_last_success(proxy_state),
        current=workspace.current,
        version=workspace.version,
        ai_enabled=_generation_provider(request, settings).available,
        rollover=_public_rollover(workspace.rollover),
        winners=winners,
        performance=keyword_outcome_analytics(db, keywords=workspace.current),
        prescriptive_mode="dormant_worked_leads",
    )


@native_router.post("/keywords/preview", response_model=KeywordDiff)
async def preview_scraper_keywords(
    payload: KeywordTextRequest,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    """Preview is a fresh read, so its version can safely gate the later save."""

    _require_scraper_grant(request, response, principal, settings)
    try:
        diff = await _scraper_operations(request, settings).preview_keywords(
            payload
        )
    except ScraperOperationsError as error:
        if error.status_code == 422 and error.detail:
            _mark_operations_success(request, settings)
            raise HTTPException(status_code=422, detail=error.detail) from None
        _audit_keyword_failure(
            db,
            principal=principal,
            action="scraper_keywords_preview_failed",
            reason="Scraper keyword preview could not reach the owning service",
            details={"outcome": "upstream_unavailable"},
        )
        return _native_unavailable(_state(request, settings))
    _mark_operations_success(request, settings)
    diff.review_token = _keyword_review_serializer(settings).dumps(
        {
            "sub": str(principal.user_id),
            "expected_version": diff.expected_version,
            "proposed_version": keyword_version(diff.proposed),
        }
    )
    return diff


@native_router.post("/keywords/save", response_model=KeywordSaveResult)
async def save_scraper_keywords(
    payload: KeywordSaveRequest,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    """Compare-and-save through the Scraper's existing atomic writer."""

    _require_scraper_grant(request, response, principal, settings)
    proposed = diff_keywords([], payload.text).proposed
    if not proposed:
        raise HTTPException(
            status_code=422,
            detail="At least one keyword is required.",
        )
    try:
        review = _keyword_review_serializer(settings).loads(
            payload.review_token,
            max_age=REVIEW_MAX_AGE_SECONDS,
        )
        reviewed_by = uuid.UUID(str(review["sub"]))
        reviewed_current = str(review["expected_version"])
        reviewed_proposal = str(review["proposed_version"])
    except (
        BadSignature,
        SignatureExpired,
        KeyError,
        TypeError,
        ValueError,
    ):
        raise HTTPException(
            status_code=422,
            detail="Preview these keyword changes again before saving.",
        ) from None
    if (
        reviewed_by != principal.user_id
        or reviewed_current != payload.expected_version
        or reviewed_proposal != keyword_version(proposed)
    ):
        raise HTTPException(
            status_code=422,
            detail="Preview these keyword changes again before saving.",
        )
    generation_id: uuid.UUID | None = None
    generation_draft = None
    if payload.generation_id:
        try:
            generation_id = uuid.UUID(payload.generation_id)
        except ValueError as error:
            raise HTTPException(
                status_code=422,
                detail="Invalid keyword generation.",
            ) from error
        generation_draft = valid_generation_draft(
            db,
            generation_id,
            administrator_id=principal.user_id,
            now=_workspace_now(request),
        )
        if generation_draft is None:
            raise HTTPException(
                status_code=422,
                detail="Invalid keyword generation.",
            )

    async with KEYWORD_WRITE_LOCK:
        operations = _scraper_operations(request, settings)
        try:
            workspace = await operations.list_keywords()
        except ScraperOperationsError:
            _audit_keyword_failure(
                db,
                principal=principal,
                action="scraper_keywords_save_failed",
                reason="Scraper keyword save could not reach the owning service",
                details={
                    "expectedVersion": payload.expected_version,
                    "proposedCount": len(proposed),
                    "enqueueRequested": payload.enqueue,
                },
            )
            return _native_unavailable(_state(request, settings))
        _mark_operations_success(request, settings)
        current = workspace.current
        current_version = keyword_version(current)
        if current_version != payload.expected_version:
            _audit_keyword_failure(
                db,
                principal=principal,
                action="scraper_keywords_save_refused",
                reason="Active Scraper keywords changed after preview",
                details={
                    "expectedVersion": payload.expected_version,
                    "currentVersion": current_version,
                    "proposedCount": len(proposed),
                    "currentCount": len(current),
                },
            )
            raise HTTPException(
                status_code=409,
                detail=(
                    "Active keywords changed after this preview. "
                    "Reload the current list and preview again."
                ),
            )

        diff = diff_keywords(current, payload.text)
        upstream_payload = payload.model_copy(
            update={
                "text": "\n".join(diff.proposed),
                "generation_id": None,
            }
        )
        try:
            await operations.save_keywords(upstream_payload)
        except ScraperOperationsError as error:
            _audit_keyword_failure(
                db,
                principal=principal,
                action="scraper_keywords_save_failed",
                reason="Scraper keyword save was refused upstream",
                details={
                    "expectedVersion": payload.expected_version,
                    "proposedCount": len(diff.proposed),
                    "enqueueRequested": payload.enqueue,
                    "upstreamStatus": error.status_code,
                },
            )
            if error.status_code == 409:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Active keywords changed after this preview. "
                        "Reload the current list and preview again."
                    ),
                ) from None
            if error.status_code == 422 and error.detail:
                raise HTTPException(
                    status_code=422,
                    detail=error.detail,
                ) from None
            return _native_unavailable(_state(request, settings))

        _mark_operations_success(request, settings)
        next_version = keyword_version(diff.proposed)
        observe_keyword_history(
            db,
            diff.proposed,
            origin="accepted_save",
            observed_at=_workspace_now(request),
        )
        if generation_draft is not None:
            accept_generation_draft(
                generation_draft,
                now=_workspace_now(request),
            )
        record_activity(
            db,
            action="scraper_keywords_saved",
            target_type="scraper_keywords",
            target_id="primary",
            actor_id=principal.user_id,
            reason="Reviewed Scraper keyword changes saved",
            details={
                "before": {
                    "version": current_version,
                    "count": len(current),
                },
                "after": {
                    "version": next_version,
                    "count": len(diff.proposed),
                },
                "addedCount": len(diff.added),
                "removedCount": len(diff.removed),
                "unchangedCount": len(diff.unchanged),
                "enqueueRequested": payload.enqueue,
                "generationAccepted": generation_draft is not None,
            },
        )
        db.commit()
        return KeywordSaveResult(
            enqueued=payload.enqueue,
            current=diff.proposed,
            version=next_version,
            diff=diff,
        )


@native_router.post(
    "/keywords/generate",
    response_model=KeywordGenerationDraft,
)
async def generate_scraper_keywords(
    payload: KeywordGenerateRequest,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    """Create a Jawnix-owned, review-only keyword draft."""

    _require_scraper_grant(request, response, principal, settings)
    provider = _generation_provider(request, settings)
    if not provider.available:
        error = generation_error(GenerationErrorCode.NOT_CONFIGURED)
        _audit_keyword_failure(
            db,
            principal=principal,
            action="scraper_keyword_generation_failed",
            reason="Jawnix keyword generation returned no review draft",
            details={
                "mode": payload.mode,
                "seedKeyword": payload.seed_keyword,
                "outcome": error.code.value,
            },
        )
        raise HTTPException(
            status_code=error.status_code,
            detail=error.message,
        )
    if not _scraper_proxy.try_generation_lock(db):
        error = generation_error(GenerationErrorCode.CONFLICT)
        _audit_keyword_failure(
            db,
            principal=principal,
            action="scraper_keyword_generation_failed",
            reason="Jawnix keyword generation was already in progress",
            details={
                "mode": payload.mode,
                "seedKeyword": payload.seed_keyword,
                "outcome": error.code.value,
            },
        )
        raise HTTPException(
            status_code=error.status_code,
            detail=error.message,
        )
    try:
        operations = _scraper_operations(request, settings)
        workspace, winners = await asyncio.gather(
            operations.list_keywords(),
            operations.keyword_winners(),
        )
    except ScraperOperationsError as error:
        _audit_keyword_failure(
            db,
            principal=principal,
            action="scraper_keyword_generation_failed",
            reason="Keyword exclusions could not be read from Scraper Operations",
            details={
                "mode": payload.mode,
                "seedKeyword": payload.seed_keyword,
                "outcome": "upstream_unavailable",
                "transportError": error.transport_error,
            },
        )
        return _native_unavailable(_state(request, settings))

    _mark_operations_success(request, settings)
    if payload.mode == "adjacent" and (
        not payload.seed_keyword
        or payload.seed_keyword.casefold()
        not in {winner.keyword.casefold() for winner in winners}
    ):
        error = generation_error(GenerationErrorCode.INVALID_SEED)
        _audit_keyword_failure(
            db,
            principal=principal,
            action="scraper_keyword_generation_failed",
            reason="Jawnix keyword generation returned no review draft",
            details={
                "mode": payload.mode,
                "seedKeyword": payload.seed_keyword,
                "outcome": error.code.value,
            },
        )
        raise HTTPException(
            status_code=error.status_code,
            detail=error.message,
        )

    history = keyword_history_terms(db)
    observed_at = _workspace_now(request)
    observe_keyword_history(
        db,
        workspace.current,
        origin="active_list",
        observed_at=observed_at,
    )
    observe_keyword_history(
        db,
        (winner.keyword for winner in winners),
        origin="winner",
        observed_at=observed_at,
    )
    exclusions, exclusion_counts = exclusion_metrics(
        active=workspace.current,
        winners=(winner.keyword for winner in winners),
        history=history,
    )
    try:
        result = await run_in_threadpool(
            provider.generate_keywords,
            mode=payload.mode,
            excluded_keywords=exclusions,
            seed_keyword=payload.seed_keyword,
        )
        if len(result.terms) != 25:
            error = generation_error(
                GenerationErrorCode.INSUFFICIENT_CANDIDATES
            )
            error.metrics = {
                **result.candidate_metrics,
                "acceptedCount": len(result.terms),
            }
            raise error
    except KeywordGenerationError as error:
        _audit_keyword_failure(
            db,
            principal=principal,
            action="scraper_keyword_generation_failed",
            reason="Jawnix keyword generation returned no review draft",
            details={
                "mode": payload.mode,
                "seedKeyword": payload.seed_keyword,
                "outcome": error.code.value,
                "statusCode": error.status_code,
                "candidateMetrics": error.metrics,
            },
        )
        raise HTTPException(
            status_code=error.status_code,
            detail=error.message,
        ) from None

    now = _workspace_now(request)
    purge_generation_drafts(db, now=now)
    draft = create_generation_draft(
        db,
        administrator_id=principal.user_id,
        mode=payload.mode,
        seed_keyword=payload.seed_keyword,
        model=provider.model,
        result=result,
        exclusion_metrics=exclusion_counts,
        now=now,
    )
    label = (
        f"keywords adjacent to {payload.seed_keyword}"
        if payload.mode == "adjacent"
        else "broad local-business keywords"
    )
    notice = (
        f"Draft ready: 25 {label}. Review below; nothing has been saved or "
        f"enqueued. {result.excluded_count} candidates were filtered."
    )
    record_activity(
        db,
        action="scraper_keyword_generation_created",
        target_type="scraper_keyword_generation",
        target_id=draft.id,
        actor_id=principal.user_id,
        reason="Generated Scraper keywords for human review",
        details={
            "mode": payload.mode,
            "seedKeyword": payload.seed_keyword,
            "keywordCount": len(result.terms),
            "excludedCount": result.excluded_count,
            "exclusionMetrics": exclusion_counts,
            "candidateMetrics": result.candidate_metrics,
            "activeConfigurationChanged": False,
        },
    )
    db.commit()
    return KeywordGenerationDraft(
        generation_id=str(draft.id),
        mode=payload.mode,
        seed_keyword=payload.seed_keyword,
        keywords=result.terms,
        excluded_count=result.excluded_count,
        notice=notice,
    )


@native_router.post(
    "/keywords/rollover",
    response_model=KeywordRollover,
)
async def control_scraper_keyword_rollover(
    payload: KeywordRolloverRequest,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    _require_scraper_grant(request, response, principal, settings)
    if (
        payload.action == "enable"
        and not _generation_provider(request, settings).available
    ):
        _audit_keyword_failure(
            db,
            principal=principal,
            action="scraper_keyword_rollover_failed",
            reason="Automatic Scraper keyword rollover could not be changed",
            details={
                "requestedAction": payload.action,
                "outcome": "not_configured",
            },
        )
        raise HTTPException(
            status_code=422,
            detail="AI generation is not configured.",
        )
    try:
        rollover = await _scraper_operations(
            request,
            settings,
        ).set_keyword_rollover(payload)
    except ScraperOperationsError as error:
        _audit_keyword_failure(
            db,
            principal=principal,
            action="scraper_keyword_rollover_failed",
            reason="Automatic Scraper keyword rollover could not be changed",
            details={
                "requestedAction": payload.action,
                "upstreamStatus": error.status_code,
            },
        )
        return _native_unavailable(_state(request, settings))
    rollover = _public_rollover(rollover)
    _mark_operations_success(request, settings)
    record_activity(
        db,
        action=(
            "scraper_keyword_rollover_enabled"
            if rollover.enabled
            else "scraper_keyword_rollover_disabled"
        ),
        target_type="scraper_keyword_rollover",
        target_id="primary",
        actor_id=principal.user_id,
        reason="Automatic Scraper keyword rollover setting changed",
        details={
            "enabled": rollover.enabled,
            "state": rollover.state,
            "percentComplete": rollover.percent_complete,
        },
    )
    db.commit()
    return rollover
