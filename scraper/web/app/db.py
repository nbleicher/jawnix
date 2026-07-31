from typing import Any

import asyncpg
from fastapi import Request


async def create_pools(app) -> None:
    settings = app.state.settings
    app.state.pool_ro = await asyncpg.create_pool(
        settings.read_dsn,
        min_size=1,
        max_size=8,
        command_timeout=settings.db_command_timeout,
        server_settings={"application_name": "gms-dashboard-ro"},
    )
    app.state.pool_rw = await asyncpg.create_pool(
        settings.database_url,
        min_size=1,
        max_size=2,
        command_timeout=settings.db_command_timeout,
        server_settings={"application_name": "gms-dashboard-rw"},
    )


async def close_pools(app) -> None:
    await app.state.pool_ro.close()
    await app.state.pool_rw.close()


async def fetch(request: Request, query: str, *args: Any, rw: bool = False):
    pool = request.app.state.pool_rw if rw else request.app.state.pool_ro
    return await pool.fetch(query, *args)


async def fetchrow(request: Request, query: str, *args: Any, rw: bool = False):
    pool = request.app.state.pool_rw if rw else request.app.state.pool_ro
    return await pool.fetchrow(query, *args)


async def fetchval(request: Request, query: str, *args: Any, rw: bool = False):
    pool = request.app.state.pool_rw if rw else request.app.state.pool_ro
    return await pool.fetchval(query, *args)
