from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from jawnix.config import Settings, get_settings
from jawnix.database import Base
from jawnix.models import Agent, CustomerProfile, LeadRequest, RequestStatus


@pytest.fixture(autouse=True, scope="session")
def _tests_do_not_read_a_developer_env_file():
    """Make the suite hermetic.

    ``Settings`` declares ``env_file=".env"``, so every construction — the
    fixtures below, the fourteen test modules that build their own, and the
    application code under test — silently inherits whatever a developer happens
    to have in the working tree. The same commit gave 492 passed with no ``.env``
    and 9 failed with a local one, because tests asserting on configurable
    behaviour (the UI flag, the Scraper proxy origin, recommendation apply mode)
    were reading ambient values rather than the ones they pin.

    Two costs. Locally it produces failures that are not real, which teaches
    people to ignore red — expensive when CI is the only gate the merged work has
    passed. And in CI the green is contingent on a file's *absence*, so it would
    flip if one ever appeared in the workspace.

    Neutralising ``env_file`` in one place fixes every construction path rather
    than fourteen call sites.
    """
    original = Settings.model_config.get("env_file")
    Settings.model_config["env_file"] = None
    # `get_settings` is lru_cached, so anything that called it during collection
    # — before fixtures run — holds a Settings built from the developer's .env.
    # Neutralising env_file alone leaves that stale instance in place.
    get_settings.cache_clear()
    try:
        yield
    finally:
        Settings.model_config["env_file"] = original
        get_settings.cache_clear()


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
