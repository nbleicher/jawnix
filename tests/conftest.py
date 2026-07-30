from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from jawnix.config import Settings, get_settings

# Make the suite hermetic.
#
# ``Settings`` declares ``env_file=".env"``, so every construction — the
# fixtures below, the test modules that build their own, and the application
# code under test — silently inherits whatever a developer happens to have in
# the working tree. One commit gave 492 passed with no ``.env`` and 9 failed
# with a local one, because tests asserting on configurable behaviour (the UI
# flag, the Scraper proxy origin, recommendation apply mode) were reading
# ambient values rather than the ones they pin.
#
# This must run at import time, not in a fixture: ``jawnix.database`` builds
# its engine from ``get_settings()`` when it is first imported, which happens
# during collection — before any fixture. A fixture-scoped patch would leave
# that engine bound to the developer's database. So: patch, purge the cache,
# and only then import anything that touches the database module.
Settings.model_config["env_file"] = None
get_settings.cache_clear()

from jawnix.database import Base  # noqa: E402
from jawnix.models import Agent, CustomerProfile, LeadRequest, RequestStatus  # noqa: E402


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as value:
        yield value
    engine.dispose()


@pytest.fixture
def settings(tmp_path):
    return Settings(
        JAWNIX_BATCH_DIR=tmp_path / "batches",
        JAWNIX_COOKIE_SECURE=False,
        JAWNIX_SESSION_SECRET="test-secret-at-least-long-enough",
    )


def make_request(session, agent: Agent, count: int, states: list[str] | None = None) -> LeadRequest:
    user_id = uuid.uuid4()
    profile = CustomerProfile(
        user_id=user_id,
        email=f"{user_id}@example.com",
        licensed_states=states or ["TX"],
        agent=agent,
    )
    request = LeadRequest(
        user_id=user_id,
        agent=agent,
        lead_count=count,
        states_snapshot=states or ["TX"],
        state_mode="all_saved",
        delivery_email=profile.email,
        status=RequestStatus.approved.value,
    )
    session.add_all([profile, request])
    session.flush()
    return request
