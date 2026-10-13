from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


def _engine_kwargs(url: str) -> dict:
    if url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False}}
    settings = get_settings()
    return {"pool_pre_ping": True, "pool_size": settings.database_pool_size, "max_overflow": settings.database_max_overflow}


settings = get_settings()
engine = create_engine(settings.database_url, **_engine_kwargs(settings.database_url))
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

# Install transaction-bound notifications without importing models during
# their own Base import. The callbacks resolve models after initialization.
from . import db_notifications  # noqa: E402,F401


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session
