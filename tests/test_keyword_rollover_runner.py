"""Jawnix-owned automatic keyword rollover."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from jawnix.keyword_generation import (
    GenerationErrorCode,
    generation_error,
)
from jawnix.keyword_rollover import (
    ROLLOVER_ACTOR,
    run_automatic_keyword_rollover,
)
from jawnix.models import AuditEntry, KeywordHistory

from scraper_fake import GenerationFake, ScraperFake

NOW = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)


def run(session, settings, fake, provider, now=NOW):
    return asyncio.run(
        run_automatic_keyword_rollover(
            session,
            settings,
            operations=fake,
            provider=provider,
            now=now,
        )
    )


def audit_actions(session) -> list[str]:
    return list(
        session.scalars(
            select(AuditEntry.action).order_by(AuditEntry.created_at)
        )
    )


def test_disabled_rollover_is_idle(session, settings):
    fake = ScraperFake()
    provider = GenerationFake()

    outcome = run(session, settings, fake, provider)

    assert outcome == {"outcome": "idle", "state": "off"}
    assert provider.calls == []
    assert fake.keyword_writes == []


def test_active_batch_is_idle(session, settings):
    fake = ScraperFake()
    fake.rollover_enabled = True

    outcome = run(session, settings, fake, GenerationFake())

    assert outcome == {"outcome": "idle", "state": "working"}
    assert fake.keyword_writes == []


def test_ready_rollover_generates_saves_and_records(session, settings):
    fake = ScraperFake()
    fake.rollover_enabled = True
    fake.rollover_state_override = "ready"
    provider = GenerationFake()
    previous = list(fake.keywords)

    outcome = run(session, settings, fake, provider)

    assert outcome == {"outcome": "generated", "activated": 25}
    assert provider.calls[0]["mode"] == "broad"
    assert len(fake.keywords) == 25
    assert fake.keyword_writes[0]["enqueue"] is True
    assert fake.rollover_events == [
        {
            "status": "generated",
            "previous_keywords": previous,
            "next_keywords": fake.keywords,
            "message": "Activated 25 automatically generated keywords",
        }
    ]
    assert audit_actions(session) == ["scraper_keyword_rollover_completed"]
    entry = session.scalars(select(AuditEntry)).one()
    assert entry.actor_user_id == ROLLOVER_ACTOR
    assert entry.details["activatedCount"] == 25
    assert entry.details["enqueued"] is True
    history_terms = set(
        session.scalars(select(KeywordHistory.term))
    )
    assert history_terms  # active list and winners were observed


def test_generation_failure_records_error_event_and_cooldown(
    session,
    settings,
):
    fake = ScraperFake()
    fake.rollover_enabled = True
    fake.rollover_state_override = "ready"
    provider = GenerationFake(
        error=generation_error(GenerationErrorCode.PROVIDER_UNAVAILABLE)
    )

    outcome = run(session, settings, fake, provider)

    assert outcome["outcome"] == "generation_failed"
    assert fake.keyword_writes == []
    assert fake.rollover_events[0]["status"] == "error"
    assert audit_actions(session) == ["scraper_keyword_rollover_failed"]

    # The recorded failure suppresses another attempt inside the cooldown.
    session.commit()
    followup = run(
        session,
        settings,
        fake,
        GenerationFake(),
        now=NOW + timedelta(minutes=5),
    )
    assert followup == {"outcome": "cooldown"}
    assert fake.keyword_writes == []

    # After the cooldown expires the rollover proceeds.
    recovered = run(
        session,
        settings,
        fake,
        GenerationFake(),
        now=NOW + timedelta(minutes=20),
    )
    assert recovered["outcome"] == "generated"


def test_unconfigured_provider_records_error(session, settings):
    fake = ScraperFake()
    fake.rollover_enabled = True
    fake.rollover_state_override = "ready"
    provider = GenerationFake(available=False)

    outcome = run(session, settings, fake, provider)

    assert outcome == {"outcome": "not_configured"}
    assert provider.calls == []
    assert fake.rollover_events == [
        {
            "status": "error",
            "previous_keywords": None,
            "next_keywords": None,
            "message": "OpenRouter is not configured for automatic rollover",
        }
    ]
    assert audit_actions(session) == ["scraper_keyword_rollover_failed"]


def test_concurrent_list_change_discards_draft(session, settings):
    class DriftingFake(ScraperFake):
        async def list_keywords(self):
            workspace = await super().list_keywords()
            # The active list changes after the workspace read.
            self.keywords = [*self.keywords, "late arrival"]
            return workspace

    fake = DriftingFake()
    fake.rollover_enabled = True
    fake.rollover_state_override = "ready"

    outcome = run(session, settings, fake, GenerationFake())

    assert outcome == {"outcome": "conflict"}
    assert fake.rollover_events == []
    assert audit_actions(session) == []
