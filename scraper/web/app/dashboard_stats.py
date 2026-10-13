"""One app-owned totals snapshot, refreshed without delaying operator reads."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from . import queries

logger = logging.getLogger(__name__)


class DashboardStatsCache:
    def __init__(self, pool, *, refresh_seconds: float = 10):
        self.pool = pool
        self.refresh_seconds = refresh_seconds
        self.data: dict | None = None
        self.fetched_at: datetime | None = None
        self.next_refresh = 0.0
        self.task: asyncio.Task | None = None

    async def refresh(self) -> None:
        captured_at = datetime.now(timezone.utc)
        try:
            row = await self.pool.fetchrow(queries.DASHBOARD_STATS)
            self.data = dict(row)
            self.fetched_at = captured_at
        except Exception as error:
            # Provider/connection errors can carry credentials. Keep the last
            # snapshot and report its actual age instead of logging the body.
            logger.warning("Dashboard totals refresh failed (%s)", type(error).__name__)
        finally:
            self.next_refresh = time.monotonic() + self.refresh_seconds

    def read(self) -> dict:
        if time.monotonic() >= self.next_refresh and (
            self.task is None or self.task.done()
        ):
            self.task = asyncio.create_task(self.refresh())
        return {"stats": self.data, "stats_fetched_at": self.fetched_at}

    async def close(self) -> None:
        if self.task is not None:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
