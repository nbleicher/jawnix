from __future__ import annotations

import asyncio
import csv
import io
import re
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse

from .. import queries
from ..auth import require_user
from ..db import fetch, fetchrow, fetchval
from .common import bridge, render

router = APIRouter(dependencies=[Depends(require_user)])
EXPORT_NAME = re.compile(r"^[A-Z0-9_-]+(?:_[0-9-]+_[0-9]+)?\.csv$")
STATE_CODE = re.compile(r"^[a-z]{2}$")
UNCATEGORIZED = "__uncategorized__"


def export_files(request: Request) -> list[dict]:
    root = request.app.state.settings.exports_dir
    root.mkdir(parents=True, exist_ok=True)
    return [
        {"name": path.name, "size": path.stat().st_size, "modified": path.stat().st_mtime}
        for path in sorted(root.glob("*.csv"), key=lambda item: item.stat().st_mtime, reverse=True)
        if EXPORT_NAME.fullmatch(path.name)
    ]


def resolve_download(request: Request, filename: str) -> Path:
    if Path(filename).name != filename or not EXPORT_NAME.fullmatch(filename):
        raise HTTPException(status_code=400, detail="Invalid export filename")
    root = request.app.state.settings.exports_dir.resolve()
    target = (root / filename).resolve()
    if target.parent != root:
        raise HTTPException(status_code=400, detail="Invalid export path")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Export not found")
    return target


async def database_states(request: Request) -> list[dict]:
    return [dict(row) for row in await fetch(request, queries.DATABASE_STATE_SUMMARIES)]


async def database_browse_context(
    request: Request, search: str = "", state: str = "", page: int = 1,
) -> dict:
    page_size = 50
    page = max(1, page)
    offset = (page - 1) * page_size
    rows = [dict(row) for row in await fetch(request, queries.BROWSE_BUSINESSES, search.strip(), state.lower().strip(), page_size, offset)]
    total = await fetchval(request, queries.BROWSE_COUNT, search.strip(), state.lower().strip())
    return {
        "businesses": rows,
        "business_total": total,
        "page": page,
        "has_next": offset + len(rows) < total,
        "search": search,
        "state": state.lower().strip(),
    }


async def database_context(request: Request, search: str = "", state: str = "", page: int = 1) -> dict:
    states = await database_states(request)
    context = await database_browse_context(request, search, state, page)
    context.update(
        database_states=states,
        database_totals=dict(await fetchrow(request, queries.DATABASE_TOTALS)),
    )
    return context


async def validate_database_state(request: Request, state: str) -> str:
    state = state.lower().strip()
    if not STATE_CODE.fullmatch(state):
        raise HTTPException(status_code=404, detail="Unknown database state")
    if not await fetchval(request, queries.DATABASE_STATE_EXISTS, state):
        raise HTTPException(status_code=404, detail="Unknown database state")
    return state


async def state_niches(request: Request, state: str) -> list[dict]:
    return [dict(row) for row in await fetch(request, queries.DATABASE_STATE_NICHES, state)]


def export_filename(state: str, niches: list[str] | None) -> str:
    if niches is None:
        label = "all"
    elif len(niches) == 1:
        label = "uncategorized" if niches[0] == UNCATEGORIZED else niches[0]
        label = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:48] or "niche"
    else:
        label = f"{len(niches)}-niches"
    day = datetime.now(timezone.utc).date().isoformat()
    return f"{state.upper()}-{label}-phone-leads-{day}.csv"


async def stream_database_csv(
    request: Request, state: str, niches: list[str] | None, include_header: bool = True,
) -> AsyncIterator[str]:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    if include_header:
        writer.writerow(["business_name", "phone_number", "state"])
    async with request.app.state.pool_ro.acquire() as connection:
        async with connection.transaction(readonly=True):
            cursor = connection.cursor(queries.DATABASE_EXPORT, state, niches, prefetch=1000)
            async for row in cursor:
                writer.writerow(row)
                if buffer.tell() >= 64 * 1024:
                    yield buffer.getvalue()
                    buffer.seek(0)
                    buffer.truncate(0)
    if buffer.tell():
        yield buffer.getvalue()


@router.get("/database")
async def database_page(request: Request, search: str = "", state: str = "", page: int = Query(1, ge=1)):
    return render(request, "database.html", **await database_context(request, search, state, page))


@router.get("/frag/database/browse")
async def database_browse(request: Request, search: str = "", state: str = "", page: int = Query(1, ge=1)):
    context = await database_browse_context(request, search, state, page)
    return render(request, "fragments/database_browse.html", **context)


@router.get("/database/states/{state}")
async def database_state_page(request: Request, state: str):
    state = await validate_database_state(request, state)
    summaries = await database_states(request)
    summary = next(item for item in summaries if item["state"].lower() == state)
    return render(
        request,
        "database_state_detail.html",
        state=state,
        summary=summary,
        niches=await state_niches(request, state),
    )


@router.get("/database/states/{state}/download")
async def download_database_state(
    request: Request,
    state: str,
    scope: Literal["all", "selected"] = Query("all"),
    keyword: Optional[List[str]] = Query(None),
):
    state = await validate_database_state(request, state)
    niches = None
    if scope == "selected":
        niches = list(dict.fromkeys(keyword or []))
        if not niches:
            raise HTTPException(status_code=422, detail="Select at least one niche")
        available = {row["niche_key"] for row in await state_niches(request, state)}
        if any(niche not in available for niche in niches):
            raise HTTPException(status_code=400, detail="Unknown niche for this state")
    filename = export_filename(state, niches)
    return StreamingResponse(
        stream_database_csv(request, state, niches),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


def bulk_export_filename(states: list[str]) -> str:
    day = datetime.now(timezone.utc).date().isoformat()
    label = "-".join(state.upper() for state in states) if len(states) <= 4 else f"{len(states)}-states"
    return f"{label}-phone-leads-{day}.csv"


async def stream_states_csv(request: Request, states: list[str]) -> AsyncIterator[str]:
    yield "business_name,phone_number,state\n"
    for state in states:
        async for chunk in stream_database_csv(request, state, None, include_header=False):
            yield chunk


@router.get("/database/bulk-download")
async def download_database_bulk(request: Request, state: Optional[List[str]] = Query(None)):
    states = list(dict.fromkeys(value.lower().strip() for value in state or [] if value.strip()))
    if not states:
        raise HTTPException(status_code=422, detail="Select at least one state")
    states = [await validate_database_state(request, value) for value in states]
    filename = bulk_export_filename(states)
    return StreamingResponse(
        stream_states_csv(request, states),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/database/download/{filename:path}")
async def download_export(request: Request, filename: str):
    target = resolve_download(request, filename)
    return FileResponse(target, media_type="text/csv", filename=target.name)


@router.post("/database/export/{state}")
async def regenerate_export(request: Request, state: str):
    state = state.lower()
    if state not in bridge(request).grid.STATE_CONFIG:
        raise HTTPException(status_code=404, detail="Unknown state")

    def generate() -> None:
        control = bridge(request)
        conn = control.export_leads.get_conn(request.app.state.settings.database_url)
        try:
            with conn.cursor() as cursor:
                control.export_leads.do_by_state(cursor, request.app.state.settings.exports_dir)
        finally:
            conn.close()

    await asyncio.to_thread(generate)
    filename = f"{state.upper()}.csv"
    resolve_download(request, filename)
    return render(request, "fragments/export_files.html", exports=export_files(request), generated=filename)
