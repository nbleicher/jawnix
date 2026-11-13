from __future__ import annotations

import asyncio
import uuid

from fastapi import Depends, HTTPException, Request
from fastapi.responses import Response
from itsdangerous import BadSignature, SignatureExpired
from sqlalchemy.orm import Session

from ..activity import record_activity
from ..auth import Principal, require_admin
from ..config import Settings, get_settings
from ..database import get_db
from ..keyword_generation import (
    KEYWORD_GENERATION_REQUEST_STALE,
    GenerationErrorCode,
    accept_generation_draft,
    generation_error,
    generation_in_progress,
    request_generation_draft,
    stale_generating_drafts,
    valid_generation_draft,
)
from ..keyword_history import observe_keyword_history
from ..models import KeywordGenerationDraftRecord
from ..scraper_keywords import (
    KeywordDiff,
    KeywordGenerateRequest,
    KeywordGenerationAccepted,
    KeywordGenerationDraft,
    KeywordGenerationStatus,
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
    status_code=202,
    response_model=KeywordGenerationAccepted,
)
def generate_scraper_keywords(
    payload: KeywordGenerateRequest,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    """Queue a Jawnix-owned, review-only keyword draft.

    The provider call can take minutes, so the keyword worker owns it; this
    route only records the request and the client polls for the draft.
    """

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
    now = _workspace_now(request)
    for stale in stale_generating_drafts(db, now=now):
        stale.acceptance_status = "failed"
        stale.failure_detail = "Keyword generation did not complete; try again"
    if generation_in_progress(db):
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
    draft = request_generation_draft(
        db,
        administrator_id=principal.user_id,
        mode=payload.mode,
        seed_keyword=payload.seed_keyword,
        model=provider.model,
        now=now,
    )
    record_activity(
        db,
        action="scraper_keyword_generation_requested",
        target_type="scraper_keyword_generation",
        target_id=draft.id,
        actor_id=principal.user_id,
        reason="Queued Scraper keyword generation for human review",
        details={
            "mode": payload.mode,
            "seedKeyword": payload.seed_keyword,
        },
    )
    db.commit()
    return KeywordGenerationAccepted(generation_id=str(draft.id))


def _generation_notice(draft: KeywordGenerationDraftRecord) -> str:
    label = (
        f"keywords adjacent to {draft.seed_keyword}"
        if draft.mode == "adjacent"
        else "broad local-business keywords"
    )
    return (
        f"Draft ready: {len(draft.terms)} {label}. Review below; nothing has "
        f"been saved or enqueued. {draft.excluded_count} candidates were "
        "filtered."
    )


@native_router.get(
    "/keywords/generate/{generation_id}",
    response_model=KeywordGenerationStatus,
)
def keyword_generation_status(
    generation_id: str,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    """The queued generation's progress; the draft itself once it is ready."""

    _require_scraper_grant(request, response, principal, settings)
    try:
        draft_id = uuid.UUID(generation_id)
    except ValueError:
        raise HTTPException(
            status_code=404, detail="Keyword generation was not found."
        ) from None
    draft = db.get(KeywordGenerationDraftRecord, draft_id)
    if draft is None or draft.administrator_id != principal.user_id:
        raise HTTPException(
            status_code=404, detail="Keyword generation was not found."
        )
    if draft.acceptance_status == "generating":
        if (
            draft.created_at
            < _workspace_now(request) - KEYWORD_GENERATION_REQUEST_STALE
        ):
            draft.acceptance_status = "failed"
            draft.failure_detail = "Keyword generation did not complete; try again"
            db.commit()
        else:
            return KeywordGenerationStatus(
                status="generating",
                generation_id=str(draft.id),
            )
    if draft.acceptance_status == "failed":
        return KeywordGenerationStatus(
            status="failed",
            generation_id=str(draft.id),
            detail=draft.failure_detail
            or generation_error(GenerationErrorCode.FAILED).message,
        )
    return KeywordGenerationStatus(
        status="ready",
        generation_id=str(draft.id),
        draft=KeywordGenerationDraft(
            generation_id=str(draft.id),
            mode=draft.mode,
            seed_keyword=draft.seed_keyword,
            keywords=draft.terms,
            excluded_count=draft.excluded_count,
            notice=_generation_notice(draft),
        ),
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
