from fastapi import APIRouter, Depends, HTTPException, Request

from .. import queries
from ..auth import require_user
from ..db import fetch
from .common import bridge, render

router = APIRouter(dependencies=[Depends(require_user)])


async def state_cards(request: Request) -> list[dict]:
    control = bridge(request)
    config = control.load_active_config()
    aggregates = {row["state"]: dict(row) for row in await fetch(request, queries.STATE_AGGREGATES)}
    cards = []
    for state in control.active_states():
        total = len(control.state_cells(state, config))
        card = aggregates.get(state, {"businesses": 0, "posted_cells": 0, "active_keywords": 0, "last_enqueued": None})
        card.update(
            state=state,
            total_cells=total,
            coverage=min(100, round(100 * card["posted_cells"] / total)) if total else 0,
        )
        cards.append(card)
    return cards


@router.get("/states")
async def states_page(request: Request):
    return render(request, "states.html", cards=await state_cards(request))


@router.get("/frag/states/cards")
async def states_cards_fragment(request: Request):
    return render(request, "fragments/state_cards.html", cards=await state_cards(request))


def validate_state(request: Request, state: str) -> str:
    state = state.lower()
    if state not in bridge(request).grid.STATE_CONFIG:
        raise HTTPException(status_code=404, detail="Unknown state")
    return state


async def keyword_rows(request: Request, state: str) -> list[dict]:
    total = len(bridge(request).state_cells(state))
    rows = []
    for row in await fetch(request, queries.STATE_KEYWORDS, state):
        item = dict(row)
        item["total_cells"] = total
        item["coverage"] = min(100, round(100 * item["posted_cells"] / total)) if total else 0
        rows.append(item)
    return rows


async def cell_rows(request: Request, state: str) -> list[dict]:
    statuses = {row["cell"]: row["status"] for row in await fetch(request, queries.STATE_CELL_STATUS, state)}
    return [{"cell": cell, "status": statuses.get(cell, "uncovered")} for cell in bridge(request).state_cells(state)]


@router.get("/states/{state}")
async def state_detail(request: Request, state: str):
    state = validate_state(request, state)
    return render(
        request,
        "state_detail.html",
        state=state,
        keywords=await keyword_rows(request, state),
        cells=await cell_rows(request, state),
    )


@router.get("/frag/states/{state}/keywords")
async def state_keywords_fragment(request: Request, state: str):
    state = validate_state(request, state)
    return render(request, "fragments/state_keywords.html", state=state, keywords=await keyword_rows(request, state))


@router.get("/frag/states/{state}/cells")
async def state_cells_fragment(request: Request, state: str):
    state = validate_state(request, state)
    return render(request, "fragments/state_cells.html", state=state, cells=await cell_rows(request, state))
