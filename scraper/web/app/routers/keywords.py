from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response

from .. import queries
from ..auth import require_user
from ..db import fetch, fetchrow, fetchval
from ..keyword_generator import GenerationError
from .common import bridge, render

router = APIRouter(dependencies=[Depends(require_user)])


async def submitted_text(request: Request, text: str, upload: Optional[UploadFile]) -> str:
    if upload and upload.filename:
        data = await upload.read(request.app.state.settings.upload_max_bytes + 1)
        if len(data) > request.app.state.settings.upload_max_bytes:
            raise HTTPException(status_code=413, detail="Keyword file is too large")
        return data.decode("utf-8", errors="replace")
    return text


def keyword_diff(control, text: str) -> dict:
    proposed = control.parse_keywords(text)
    current = control.load_keywords()
    current_keys = {item.casefold() for item in current}
    proposed_keys = {item.casefold() for item in proposed}
    return {
        "proposed": proposed,
        "added": [item for item in proposed if item.casefold() not in current_keys],
        "removed": [item for item in current if item.casefold() not in proposed_keys],
        "unchanged": [item for item in proposed if item.casefold() in current_keys],
    }


def base_context(request: Request, **values) -> dict:
    current = bridge(request).load_keywords()
    return {
        "current": current,
        "keyword_text": "\n".join(current),
        "ai_enabled": request.app.state.settings.keyword_ai_enabled,
        **values,
    }


async def rollover_context(request: Request) -> dict:
    control = bridge(request)
    current = control.load_keywords()
    states = control.active_states()
    path = request.app.state.settings.keywords_path
    started_on = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).date()
    expected_jobs = len(current) * sum(len(control.state_cells(state)) for state in states)
    expected_pairs = len(current) * len(states)
    keyword_keys = [value.casefold() for value in current]
    progress = dict(await fetchrow(
        request, queries.AUTO_KEYWORD_ROLLOVER_PROGRESS,
        keyword_keys, states, started_on,
    ))
    status = dict(await fetchrow(request, queries.AUTO_KEYWORD_ROLLOVER_STATE))
    posted_jobs = int(progress["posted_jobs"] or 0)
    active_jobs = int(progress["active_jobs"] or 0)
    completed_pairs = int(progress["completed_pairs"] or 0)
    if not status["enabled"]:
        state = {"key": "off", "label": "Off", "detail": "Manual keyword batches"}
    elif posted_jobs < expected_jobs or completed_pairs < expected_pairs:
        state = {
            "key": "working", "label": "Current batch active",
            "detail": f"{posted_jobs:,} of {expected_jobs:,} coverage jobs enqueued",
        }
    elif active_jobs:
        state = {
            "key": "draining", "label": "Finishing current batch",
            "detail": f"{active_jobs:,} queued or running jobs remain",
        }
    else:
        state = {"key": "ready", "label": "Rollover ready", "detail": "Next generation check is due"}
    percent_complete = min(100, round(100 * posted_jobs / expected_jobs)) if expected_jobs else 0
    return {
        "rollover": status,
        "rollover_state": state,
        "rollover_progress": {
            **progress, "expected_jobs": expected_jobs, "expected_pairs": expected_pairs,
            "percent_complete": percent_complete,
        },
    }


@router.get("/keywords")
async def keywords_page(request: Request, draft: str = ""):
    context = base_context(request)
    context.update(await rollover_context(request))
    if draft:
        try:
            draft_id = UUID(draft)
        except ValueError as error:
            raise HTTPException(status_code=404, detail="Keyword draft not found") from error
        row = await fetchrow(
            request, queries.KEYWORD_DRAFT, str(draft_id),
            str(request.app.state.settings.keyword_draft_ttl_hours),
        )
        if not row:
            raise HTTPException(status_code=404, detail="Keyword draft not found or expired")
        generation = dict(row)
        if isinstance(generation["keywords"], str):
            generation["keywords"] = json.loads(generation["keywords"])
        context.update(
            keyword_text="\n".join(generation["keywords"]),
            generation=generation,
            generation_id=str(generation["id"]),
        )
    return render(request, "keywords.html", **context)


@router.get("/frag/keywords/auto-rollover")
async def auto_rollover_status(request: Request):
    return render(
        request, "fragments/keyword_rollover.html", **await rollover_context(request),
    )


@router.post("/keywords/auto-rollover")
async def set_auto_rollover(request: Request, action: str = Form(...)):
    if action not in {"enable", "disable"}:
        raise HTTPException(status_code=422, detail="Unsupported rollover action")
    if action == "enable" and not request.app.state.settings.keyword_ai_enabled:
        raise HTTPException(status_code=422, detail="AI generation is not configured")
    await fetchval(
        request, queries.SET_AUTO_KEYWORD_ROLLOVER,
        "true" if action == "enable" else "false", rw=True,
    )
    return render(
        request, "fragments/keyword_rollover.html", **await rollover_context(request),
    )


@router.get("/keywords/winners")
async def keyword_winners(request: Request):
    winners = [dict(row) for row in await fetch(request, queries.KEYWORD_WINNERS)]
    return render(request, "keyword_winners.html", **base_context(request, winners=winners))


@router.post("/keywords/generate")
async def generate_keywords(
    request: Request,
    mode: str = Form(default="broad"),
    seed_keyword: str = Form(default=""),
):
    if mode not in {"broad", "adjacent"}:
        return render(request, "fragments/generation_feedback.html", error="Unsupported generation mode")
    rows = await fetch(request, queries.USED_KEYWORDS)
    used = {str(row["keyword"]).strip() for row in rows if row["keyword"]}
    used.update(bridge(request).load_keywords())
    seed = seed_keyword.strip() or None
    if mode == "adjacent":
        winners = await fetch(request, queries.KEYWORD_WINNERS)
        winner_keys = {str(row["keyword"]).casefold() for row in winners}
        if not seed or seed.casefold() not in winner_keys:
            return render(request, "fragments/generation_feedback.html", error="The selected winner is unavailable")
    try:
        result = await request.app.state.keyword_generator.generate(mode, used, seed)
    except GenerationError as error:
        return render(request, "fragments/generation_feedback.html", error=str(error))

    generation_id = uuid4()
    await fetchval(
        request, queries.INSERT_KEYWORD_GENERATION,
        str(generation_id), mode, seed, request.app.state.settings.openrouter_model,
        json.dumps(result.keywords), result.excluded_count, rw=True,
    )
    await fetch(request, queries.CLEANUP_KEYWORD_GENERATIONS, rw=True)
    location = f"/keywords?draft={generation_id}"
    if request.headers.get("HX-Request") == "true":
        return Response(status_code=204, headers={"HX-Redirect": location})
    return RedirectResponse(location, status_code=303)


@router.post("/keywords/preview")
async def preview_keywords(
    request: Request,
    text: str = Form(default=""),
    upload: Optional[UploadFile] = File(default=None),
):
    body = await submitted_text(request, text, upload)
    return render(request, "fragments/keyword_preview.html", **keyword_diff(bridge(request), body))


@router.post("/keywords/save")
async def save_keywords(
    request: Request,
    text: str = Form(default=""),
    upload: Optional[UploadFile] = File(default=None),
    enqueue: bool = Form(default=False),
    generation_id: str = Form(default=""),
):
    control = bridge(request)
    body = await submitted_text(request, text, upload)
    keywords = control.parse_keywords(body)
    if not keywords:
        raise HTTPException(status_code=422, detail="At least one keyword is required")
    await control.atomic_write(request.app.state.settings.keywords_path, "\n".join(keywords) + "\n")
    if enqueue:
        await control.trigger_enqueue()
    if generation_id:
        try:
            accepted_id = UUID(generation_id)
        except ValueError as error:
            raise HTTPException(status_code=422, detail="Invalid keyword generation") from error
        await fetchval(request, queries.ACCEPT_KEYWORD_GENERATION, str(accepted_id), rw=True)
    return render(
        request,
        "fragments/keyword_preview.html",
        proposed=keywords,
        added=[],
        removed=[],
        unchanged=keywords,
        saved=True,
        enqueued=enqueue,
    )
