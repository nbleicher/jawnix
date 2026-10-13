import asyncio

import pytest

from app.dashboard_stats import DashboardStatsCache


@pytest.mark.asyncio
async def test_stale_reads_share_one_background_refresh(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("app.dashboard_stats.time.monotonic", lambda: clock[0])
    release = asyncio.Event()

    class Pool:
        calls = 0

        async def fetchrow(self, query):
            self.calls += 1
            if self.calls > 1:
                await release.wait()
            return {"businesses": self.calls}

    pool = Pool()
    cache = DashboardStatsCache(pool)
    try:
        await cache.refresh()
        first = cache.read()
        clock[0] = 11
        assert cache.read() == first
        assert cache.read() == first
        await asyncio.sleep(0)
        assert pool.calls == 2
        assert cache.read() == first
        release.set()
        await cache.task
        assert cache.read()["stats"]["businesses"] == 2
    finally:
        await cache.close()


@pytest.mark.asyncio
async def test_refresh_failure_preserves_the_last_capture_time(caplog):
    class Pool:
        fail = False

        async def fetchrow(self, query):
            if self.fail:
                raise RuntimeError("private database credential")
            return {"businesses": 42}

    pool = Pool()
    cache = DashboardStatsCache(pool)
    await cache.refresh()
    first = cache.read()
    pool.fail = True
    await cache.refresh()
    assert cache.read() == first
    assert "private database credential" not in caplog.text
    await cache.close()


@pytest.mark.asyncio
async def test_shutdown_cancels_an_outstanding_refresh():
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class Pool:
        async def fetchrow(self, query):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    cache = DashboardStatsCache(Pool())
    cache.read()
    await started.wait()
    await cache.close()
    assert cancelled.is_set()
