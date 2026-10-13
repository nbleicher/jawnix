"""Rollover must retain useful work across failed model runs and restarts."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy.orm import Session

from jawnix import models
from jawnix.config import Settings
from jawnix.keyword_generation import OpenRouterGenerationProvider
from jawnix.keyword_rollover import run_automatic_keyword_rollover
from jawnix.keyword_history import observe_keyword_history
from jawnix.scraper_operations import ScraperOperationsError
from scraper_fake import ScraperFake


NOW = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)


def terms(start, count):
    return [
        "Trade " + hashlib.sha256(str(i).encode()).hexdigest()[:10]
        for i in range(start, start + count)
    ]


def completion(candidates):
    return httpx.Response(
        200,
        json={
            "usage": {"prompt_tokens": 13517, "completion_tokens": 110, "cost": 0.017},
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": json.dumps({"keywords": candidates})},
                }
            ],
        },
    )


def provider(settings, handler):
    config = settings.model_copy(
        update={
            "openrouter_api_key": Settings(
                OPENROUTER_API_KEY="fixture-key"
            ).openrouter_api_key
        }
    )
    return OpenRouterGenerationProvider(config, transport=httpx.MockTransport(handler))


def run(session, settings, operations, model, now=NOW):
    return asyncio.run(
        run_automatic_keyword_rollover(
            session, settings, operations=operations, provider=model, now=now
        )
    )


def pool(session):
    return session.get(models.KeywordCandidatePool, 1)


def test_partial_generation_survives_a_new_database_session(
    session, settings, monkeypatch
):
    monkeypatch.setattr("jawnix.keyword_generation.KEYWORD_GENERATION_MAX_ATTEMPTS", 1)
    operations = ScraperFake()
    operations.rollover_enabled = True
    operations.rollover_state_override = "ready"
    existing = list(operations.keywords)
    first = provider(settings, lambda request: completion([*existing, *terms(1, 3)]))

    outcome = run(session, settings, operations, first)

    assert outcome["outcome"] == "pooling"
    assert outcome["available"] == 3
    assert operations.keyword_writes == []
    entry = (
        session.query(models.AuditEntry)
        .filter_by(action="scraper_keyword_pool_refilled")
        .one()
    )
    usage = entry.details["candidateMetrics"]["attempts"][0]["usage"]
    assert usage["inputUnits"] == 13517
    assert usage["outputUnits"] == 110
    assert usage["cost"] == 0.017
    session.commit()
    with Session(session.get_bind(), expire_on_commit=False) as restarted:
        assert [item["term"] for item in pool(restarted).candidates] == terms(1, 3)
        second = provider(
            settings, lambda request: completion([*existing, *terms(100, 4)])
        )
        followup = run(
            restarted, settings, operations, second, NOW + timedelta(minutes=1)
        )
        assert followup["available"] == 7
        assert {item["term"] for item in pool(restarted).candidates} == set(
            terms(1, 3) + terms(100, 4)
        )
        assert operations.keyword_writes == []


def test_candidates_are_generated_while_the_active_batch_is_working(
    session, settings, monkeypatch
):
    monkeypatch.setattr("jawnix.keyword_generation.KEYWORD_GENERATION_MAX_ATTEMPTS", 1)
    operations = ScraperFake()
    operations.rollover_enabled = True
    operations.rollover_state_override = "working"
    model = provider(settings, lambda request: completion(terms(1, 10)))

    outcome = run(session, settings, operations, model)

    assert outcome["outcome"] == "pooling"
    assert outcome["available"] == 10
    assert operations.keyword_writes == []


def test_campaign_drains_while_candidates_are_being_generated(session, settings):
    operations = ScraperFake()
    operations.rollover_enabled = True
    operations.rollover_state_override = "working"
    session.add(models.KeywordCandidatePool(
        id=1, candidates=[{"term": term, "model": "previous/model"} for term in terms(1, 20)],
    ))
    session.flush()

    def generated(request):
        # The fleet finishes its last jobs while the provider call is in flight.
        operations.rollover_state_override = "ready"
        return completion(terms(100, 10))

    outcome = run(session, settings, operations, provider(settings, generated))

    assert outcome == {"outcome": "generated", "activated": 25}
    assert operations.keyword_writes[0]["enqueue"] is True
    assert len(pool(session).candidates) == 5


def test_prepared_batch_does_not_start_generation_while_last_jobs_drain(session, settings):
    operations = ScraperFake()
    operations.rollover_enabled = True
    operations.rollover_state_override = "draining"
    session.add(models.KeywordCandidatePool(
        id=1, candidates=[{"term": term, "model": "previous/model"} for term in terms(1, 25)],
    ))
    session.flush()

    def unexpected(request):
        raise AssertionError("Generation would delay an already-prepared handoff")

    outcome = run(session, settings, operations, provider(settings, unexpected))

    assert outcome["outcome"] == "pooling"
    assert operations.keyword_writes == []


def test_ready_batch_uses_the_pool_even_when_the_provider_is_unavailable(
    session, settings
):
    session.add(
        models.KeywordCandidatePool(
            id=1,
            candidates=[
                {"term": term, "model": "previous/model", "focus": "manufacturing"}
                for term in terms(1, 25)
            ],
        )
    )
    session.commit()
    operations = ScraperFake()
    operations.rollover_enabled = True
    operations.rollover_state_override = "ready"
    unavailable = OpenRouterGenerationProvider(settings)

    outcome = run(session, settings, operations, unavailable)

    assert outcome == {"outcome": "generated", "activated": 25}
    assert operations.keywords == terms(1, 25)
    assert pool(session).candidates == []
    assert operations.keyword_writes[0]["enqueue"] is True


def test_conflicting_activation_keeps_candidates_for_revalidation(session, settings):
    class ChangedWorkspace(ScraperFake):
        async def save_keywords(self, payload):
            self.keywords = ["changed by another administrator"]
            return await super().save_keywords(payload)

    session.add(
        models.KeywordCandidatePool(
            id=1,
            candidates=[
                {"term": term, "model": "previous/model", "focus": "manufacturing"}
                for term in terms(1, 25)
            ],
        )
    )
    session.commit()
    operations = ChangedWorkspace()
    operations.rollover_enabled = True
    operations.rollover_state_override = "ready"

    outcome = run(session, settings, operations, OpenRouterGenerationProvider(settings))

    assert outcome == {"outcome": "conflict"}
    assert [item["term"] for item in pool(session).candidates] == terms(1, 25)


def test_generation_uses_small_industry_focused_requests(settings):
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        return completion(terms(1, 10))

    result = provider(settings, handler).generate_keywords(
        mode="broad", excluded_keywords=[], count=10
    )

    assert result.terms == terms(1, 10)
    instruction = json.loads(requests[0]["messages"][1]["content"])
    assert 1 <= instruction["candidate_count"] <= 15
    assert instruction["industry_focus"]


def test_business_names_are_audit_values_rather_than_secret_field_names(
    session, settings
):
    operations = ScraperFake()
    operations.rollover_enabled = True
    operations.rollover_state_override = "working"
    candidates = ["Signature stamp engravers", *terms(1, 9)]

    outcome = run(
        session,
        settings,
        operations,
        provider(settings, lambda request: completion(candidates)),
    )
    session.commit()

    assert outcome["available"] == 10
    assert pool(session).candidates[0]["term"] == "Signature stamp engravers"


def test_adjacent_generation_keeps_the_seed_as_its_industry_focus(settings):
    instructions = []

    def handler(request):
        payload = json.loads(request.content)
        instructions.append(json.loads(payload["messages"][1]["content"]))
        return completion(terms(1, 10))

    provider(settings, handler).generate_keywords(
        mode="adjacent", excluded_keywords=[], seed_keyword="Wedding Cakes", count=10
    )

    assert "Wedding Cakes" in instructions[0]["industry_focus"]


def test_low_valid_yield_switches_models_without_losing_accepted_terms(
    settings, monkeypatch
):
    monkeypatch.setattr("jawnix.keyword_generation.KEYWORD_GENERATION_MAX_ATTEMPTS", 2)
    config = settings.model_copy(
        update={"openrouter_keyword_fallback_models": "fixture/fallback"}
    )
    models_called = []
    historical = terms(1, 8)

    def handler(request):
        payload = json.loads(request.content)
        models_called.append(payload["model"])
        if payload["model"] != "fixture/fallback":
            return completion([*historical, *terms(100, 2)])
        return completion(terms(200, 8))

    result = provider(config, handler).generate_keywords(
        mode="broad", excluded_keywords=historical, count=10
    )

    assert models_called == [settings.openrouter_model, "fixture/fallback"]
    assert result.terms == terms(100, 2) + terms(200, 8)


def test_pool_revalidation_removes_terms_used_since_the_last_refill(session, settings):
    prepared = terms(1, 26)
    session.add(
        models.KeywordCandidatePool(
            id=1,
            candidates=[{"term": term, "model": "previous/model"} for term in prepared],
        )
    )
    observe_keyword_history(
        session, prepared[:1], origin="active_list", observed_at=NOW
    )
    session.commit()
    operations = ScraperFake()
    operations.rollover_enabled = True
    operations.rollover_state_override = "ready"

    outcome = run(session, settings, operations, OpenRouterGenerationProvider(settings))

    assert outcome == {"outcome": "generated", "activated": 25}
    assert operations.keywords == prepared[1:]


def test_event_failure_after_activation_still_commits_history_and_removes_pool(
    session, settings
):
    class UnavailableEvents(ScraperFake):
        async def record_keyword_rollover_event(self, payload):
            raise ScraperOperationsError(
                detail="Event service unavailable", status_code=503
            )

    prepared = terms(1, 25)
    session.add(
        models.KeywordCandidatePool(
            id=1,
            candidates=[{"term": term, "model": "previous/model"} for term in prepared],
        )
    )
    session.commit()
    operations = UnavailableEvents()
    operations.rollover_enabled = True
    operations.rollover_state_override = "ready"

    outcome = run(session, settings, operations, OpenRouterGenerationProvider(settings))
    session.commit()

    assert outcome == {"outcome": "generated", "activated": 25}
    with Session(session.get_bind(), expire_on_commit=False) as restarted:
        assert pool(restarted).candidates == []
        assert restarted.query(models.KeywordHistory).count() >= 25


def test_credit_failure_preserves_partial_work_and_stops_fallback_requests(
    session, settings
):
    operations = ScraperFake()
    operations.rollover_enabled = True
    operations.rollover_state_override = "ready"
    history = terms(1, 6)
    observe_keyword_history(session, history, origin="active_list", observed_at=NOW)
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return completion([*history, *terms(100, 4)])
        return httpx.Response(402, json={"error": {"message": "Insufficient credit"}})

    config = settings.model_copy(
        update={"openrouter_keyword_fallback_models": "fixture/fallback"}
    )
    outcome = run(session, config, operations, provider(config, handler))

    assert outcome == {"outcome": "pooling", "available": 4, "added": 4}
    assert len(requests) == 2
    assert all(request["model"] == settings.openrouter_model for request in requests)
    assert pool(session).next_refill_at == NOW + timedelta(minutes=15)
    entry = (
        session.query(models.AuditEntry)
        .filter_by(action="scraper_keyword_pool_refilled")
        .one()
    )
    assert entry.details["candidateMetrics"]["candidateCount"] == 10


def test_history_import_during_generation_is_rechecked_before_activation(
    session, settings
):
    prepared = terms(1, 15)
    session.add(
        models.KeywordCandidatePool(
            id=1,
            candidates=[{"term": term, "model": "previous/model"} for term in prepared],
        )
    )
    session.commit()
    operations = ScraperFake()
    operations.rollover_enabled = True
    operations.rollover_state_override = "ready"

    def handler(request):
        observe_keyword_history(
            session, prepared[:1], origin="active_list", observed_at=NOW
        )
        return completion(terms(100, 10))

    outcome = run(session, settings, operations, provider(settings, handler))

    assert outcome["outcome"] == "pooling"
    assert outcome["available"] == 24
    assert operations.keyword_writes == []
    assert prepared[0] not in [item["term"] for item in pool(session).candidates]


def test_work_started_during_generation_delays_activation(session, settings):
    prepared = terms(1, 15)
    session.add(
        models.KeywordCandidatePool(
            id=1,
            candidates=[{"term": term, "model": "previous/model"} for term in prepared],
        )
    )
    session.commit()
    operations = ScraperFake()
    operations.rollover_enabled = True
    operations.rollover_state_override = "ready"

    def handler(request):
        operations.rollover_state_override = "working"
        return completion(terms(100, 10))

    outcome = run(session, settings, operations, provider(settings, handler))

    assert outcome["outcome"] == "pooling"
    assert outcome["available"] == 25
    assert operations.keyword_writes == []


def test_failed_activation_revalidation_keeps_newly_generated_candidates(
    session, settings
):
    class UnavailableReread(ScraperFake):
        reads = 0

        async def list_keywords(self):
            self.reads += 1
            if self.reads == 2:
                raise ScraperOperationsError(status_code=503)
            return await super().list_keywords()

    prepared = terms(1, 15)
    session.add(
        models.KeywordCandidatePool(
            id=1,
            candidates=[{"term": term, "model": "previous/model"} for term in prepared],
        )
    )
    session.commit()
    operations = UnavailableReread()
    operations.rollover_enabled = True
    operations.rollover_state_override = "ready"

    outcome = run(
        session,
        settings,
        operations,
        provider(settings, lambda request: completion(terms(100, 10))),
    )
    session.commit()

    assert outcome == {"outcome": "revalidation_failed"}
    assert operations.keyword_writes == []
    with Session(session.get_bind(), expire_on_commit=False) as restarted:
        assert len(pool(restarted).candidates) == 25
