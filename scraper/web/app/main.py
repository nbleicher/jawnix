from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from .auth import require_control_token, valid_control_token
from .config import Settings
from .contracts import Health
from .control_api import router as control_router
from .control_bridge import ControlBridge
from .db import close_pools, create_pools
from .keyword_generator import KeywordGenerator


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.control = ControlBridge(settings)
        app.state.keyword_generator = KeywordGenerator(settings)
        await create_pools(app)
        yield
        await close_pools(app)

    app = FastAPI(
        title="Scraper Control",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def authenticate_every_request(request: Request, call_next):
        if not valid_control_token(
            request, request.headers.get("authorization")
        ):
            return JSONResponse(
                {"detail": "Authentication required"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await call_next(request)

    app.include_router(control_router)

    @app.get(
        "/healthz",
        response_model=Health,
        dependencies=[Depends(require_control_token)],
    )
    async def healthz(request: Request):
        async with request.app.state.pool_ro.acquire() as connection:
            await connection.fetchval("SELECT 1")
        return Health(status="ok")

    return app


app = create_app()
