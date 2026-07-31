from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import Settings
from .control_bridge import ControlBridge
from .db import close_pools, create_pools
from .keyword_generator import KeywordGenerator
from .routers import configure, dashboard, database, history, keywords, states

APP_DIR = Path(__file__).resolve().parent


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.control = ControlBridge(settings)
        app.state.keyword_generator = KeywordGenerator(settings)
        app.state.templates = Jinja2Templates(directory=APP_DIR / "templates")
        await create_pools(app)
        yield
        await close_pools(app)

    app = FastAPI(title="GMS Operations", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
    app.include_router(dashboard.router)
    app.include_router(states.router)
    app.include_router(keywords.router)
    app.include_router(history.router)
    app.include_router(configure.router)
    app.include_router(database.router)

    @app.get("/healthz")
    async def healthz(request: Request):
        async with request.app.state.pool_ro.acquire() as connection:
            await connection.fetchval("SELECT 1")
        return {"status": "ok"}

    @app.get("/")
    async def root():
        return RedirectResponse("/dashboard", status_code=307)

    return app


app = create_app()
