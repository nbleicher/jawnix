from fastapi import APIRouter, Depends, Query, Request

from .. import queries
from ..auth import require_user
from ..db import fetch
from .common import render

router = APIRouter(dependencies=[Depends(require_user)])

SORT_COLUMNS = {
    "keyword": "h.keyword",
    "state": "h.state",
    "last_enqueued": "h.last_enqueued",
    "cells_posted": "cells_posted",
    "latest_enqueued": "latest_enqueued",
}

# Sorts that can be decided from keyword_history alone page first, then join.
# Aggregate sorts still need the full join (rare operator path).
PAGE_SORT_COLUMNS = {
    "keyword": "h.keyword",
    "state": "h.state",
    "last_enqueued": "h.last_enqueued",
}


async def history_rows(request: Request, search: str, state: str, sort: str, direction: str):
    order = "ASC" if direction.lower() == "asc" else "DESC"
    page_column = PAGE_SORT_COLUMNS.get(sort)
    if page_column is not None:
        final_column = {
            "keyword": "keyword",
            "state": "state",
            "last_enqueued": "last_enqueued",
        }[sort]
        sql = (
            queries.HISTORY_PAGE.format(
                page_order=f"{page_column} {order}",
            )
            + f" ORDER BY {final_column} {order} NULLS LAST"
        )
    else:
        column = SORT_COLUMNS.get(sort, SORT_COLUMNS["last_enqueued"])
        sql = f"{queries.HISTORY_BASE} ORDER BY {column} {order} NULLS LAST LIMIT 500"
    return [dict(row) for row in await fetch(request, sql, search.strip(), state.lower().strip())]


@router.get("/history")
async def history_page(
    request: Request,
    search: str = "",
    state: str = "",
    sort: str = "last_enqueued",
    direction: str = Query(default="desc", pattern="^(asc|desc)$"),
):
    rows = await history_rows(request, search, state, sort, direction)
    return render(request, "history.html", rows=rows, search=search, state=state, sort=sort, direction=direction)


@router.get("/frag/history/table")
async def history_fragment(
    request: Request,
    search: str = "",
    state: str = "",
    sort: str = "last_enqueued",
    direction: str = Query(default="desc", pattern="^(asc|desc)$"),
):
    rows = await history_rows(request, search, state, sort, direction)
    return render(request, "fragments/history_table.html", rows=rows)
