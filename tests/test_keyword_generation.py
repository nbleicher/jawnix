from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from jawnix.config import Settings
from jawnix.keyword_generation import (
    GenerationErrorCode,
    KeywordGenerationError,
    KeywordGenerationResult,
    OpenRouterGenerationProvider,
    accept_generation_draft,
    create_generation_draft,
    exclusion_metrics,
    purge_generation_drafts,
    try_generation_lock,
    valid_generation_draft,
)
from jawnix.models import KeywordGenerationDraftRecord, KeywordHistory


def generation_settings(**overrides) -> Settings:
    values = {
        "OPENROUTER_API_KEY": "secret-provider-key",
        "OPENROUTER_MODEL": "test/model",
        "OPENROUTER_BASE_URL": "https://openrouter.test/api/v1",
        "JAWNIX_KEYWORD_GENERATION_DEADLINE_SECONDS": 180,
    }
    values.update(overrides)
    return Settings(**values)


def test_generation_deadline_replaces_the_upstream_generation_timeout():
    settings = Settings()
    assert settings.keyword_generation_deadline_seconds == 180
    assert not hasattr(settings, "scraper_ops_generation_timeout_seconds")
    environment = Path(".env.example").read_text(encoding="utf-8")
    assert "JAWNIX_KEYWORD_GENERATION_DEADLINE_SECONDS=180" in environment
    assert "JAWNIX_SCRAPER_OPS_GENERATION_TIMEOUT_SECONDS" not in environment
    acquisition_environment = Path("scraper/.env.box.example").read_text(
        encoding="utf-8"
    )
    acquisition_compose = Path("scraper/docker-compose.box.yml").read_text(
        encoding="utf-8"
    )
    assert "OPENROUTER_API_KEY" not in acquisition_environment
    assert "OPENROUTER_API_KEY" not in acquisition_compose


def test_openrouter_key_is_secret_typed_and_never_serializes_as_plain_text():
    settings = generation_settings()
    assert "secret-provider-key" not in repr(settings)
    assert str(settings.openrouter_api_key) == "**********"


def candidates(start: int, count: int) -> list[str]:
    return [
        "Trade " + hashlib.sha256(str(index).encode()).hexdigest()[:10]
        for index in range(start, start + count)
    ]


def completion(terms: list[str], *, finish_reason: str = "stop") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "finish_reason": finish_reason,
                    "message": {"content": json.dumps({"keywords": terms})},
                }
            ]
        },
    )


def test_exact_near_and_historical_duplicates_are_filtered_with_adaptive_context():
    requests: list[dict] = []
    first = [
        "PLUMBERS",
        "Roof Repairs",
        "Wedding Cakes",
        "Wedding Cake",
        *candidates(1, 20),
    ]
    second = [*candidates(1, 5), *candidates(100, 30)]
    responses = iter((completion(first), completion(second)))

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return next(responses)

    provider = OpenRouterGenerationProvider(
        generation_settings(),
        transport=httpx.MockTransport(handler),
    )
    result = provider.generate_keywords(
        mode="broad",
        excluded_keywords=["plumbers", "roof repair"],
    )

    assert len(result.terms) == 25
    assert len({term.casefold() for term in result.terms}) == 25
    assert "PLUMBERS" not in result.terms
    assert "Roof Repairs" not in result.terms
    assert not ({"Wedding Cakes", "Wedding Cake"} <= set(result.terms))
    assert result.candidate_metrics["attemptCount"] == 2
    assert result.candidate_metrics["rejectionReasons"]["duplicate"] >= 4
    retry = json.loads(requests[1]["messages"][1]["content"])["retry_context"]
    assert retry["accepted_keywords"] == result.terms[
        : len(retry["accepted_keywords"])
    ]
    assert len(retry["accepted_keywords"]) < 25
    assert any(item["candidate"] == "PLUMBERS" for item in retry["rejected_candidates"])


def test_production_sized_history_is_visible_to_generation():
    requests: list[dict] = []
    historical = [f"Historical Niche {index:04d}" for index in range(2_853)]

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        instruction = json.loads(payload["messages"][1]["content"])
        requested = instruction["candidate_count"]
        visible = set(instruction["excluded_keywords"])
        hidden = [term for term in historical if term not in visible]
        if hidden:
            call = len(requests)
            terms = [
                *hidden[: requested - 2],
                *candidates(10_000 + call * 100, 2),
            ]
        else:
            terms = candidates(20_000, requested)
        return completion(terms)

    provider = OpenRouterGenerationProvider(
        generation_settings(),
        transport=httpx.MockTransport(handler),
    )
    result = provider.generate_keywords(
        mode="broad",
        excluded_keywords=historical,
    )

    assert len(result.terms) == 25
    first_instruction = json.loads(requests[0]["messages"][1]["content"])
    assert set(first_instruction["excluded_keywords"]) == set(historical)


@pytest.mark.parametrize(
    ("responses", "code", "message", "expected_calls"),
    [
        (
            [httpx.Response(200, json={"choices": [{"message": {"content": "not-json"}}]})] * 3,
            GenerationErrorCode.MALFORMED,
            "malformed keyword data",
            3,
        ),
        (
            [completion([], finish_reason="length")] * 3,
            GenerationErrorCode.TRUNCATED,
            "output limit",
            3,
        ),
        (
            [httpx.Response(429, json={"error": {"message": "secret detail"}})] * 3,
            GenerationErrorCode.RATE_LIMITED,
            "rate limiting",
            1,
        ),
    ],
)
def test_provider_failures_retry_at_most_three_times_without_leaking_details(
    responses,
    code,
    message,
    expected_calls,
):
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        response = responses[calls]
        calls += 1
        return response

    provider = OpenRouterGenerationProvider(
        generation_settings(),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(KeywordGenerationError) as raised:
        provider.generate_keywords(mode="broad", excluded_keywords=[])

    assert raised.value.code == code
    assert message in raised.value.message
    assert calls == expected_calls
    assert raised.value.metrics["attemptCount"] == expected_calls
    assert "secret-provider-key" not in raised.value.message
    assert "secret detail" not in raised.value.message


def test_transport_timeout_is_typed_and_retried_within_the_operation_budget():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("private timeout", request=request)

    provider = OpenRouterGenerationProvider(
        generation_settings(),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(KeywordGenerationError) as raised:
        provider.generate_keywords(mode="broad", excluded_keywords=[])

    assert raised.value.code == GenerationErrorCode.TIMEOUT
    assert calls == 3
    assert "private timeout" not in raised.value.message


def test_one_deadline_covers_the_whole_operation_instead_of_each_attempt():
    clock = SimpleNamespace(now=0.0)
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        clock.now = 2.0
        return completion(candidates(1, 50))

    provider = OpenRouterGenerationProvider(
        generation_settings(JAWNIX_KEYWORD_GENERATION_DEADLINE_SECONDS=1),
        transport=httpx.MockTransport(handler),
        clock=lambda: clock.now,
    )
    with pytest.raises(KeywordGenerationError) as raised:
        provider.generate_keywords(mode="broad", excluded_keywords=[])

    assert raised.value.code == GenerationErrorCode.TIMEOUT
    assert calls == 1


def test_strict_exactly_25_failure_never_pads_or_returns_a_partial_result():
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return completion(["plumbers", "roof repairs", "x"])

    provider = OpenRouterGenerationProvider(
        generation_settings(),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(KeywordGenerationError) as raised:
        provider.generate_keywords(
            mode="broad",
            excluded_keywords=["Plumbers", "Roof Repair"],
        )

    assert raised.value.code == GenerationErrorCode.INSUFFICIENT_CANDIDATES
    assert raised.value.message == (
        "AI could not produce 25 sufficiently distinct keywords; try again"
    )
    assert raised.value.metrics["acceptedCount"] == 0
    assert calls == 3


def test_niche_proposals_retry_malformed_output_behind_the_same_interface():
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        content = (
            "not-json"
            if calls == 1
            else json.dumps(
                {"proposals": [{"id": "OH::roofer", "niche": "Roofing"}]}
            )
        )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}}]},
        )

    provider = OpenRouterGenerationProvider(
        generation_settings(),
        transport=httpx.MockTransport(handler),
    )
    assert provider.propose_niches(
        [{"id": "OH::roofer", "keyword": "roofer", "state": "OH"}]
    ) == [{"id": "OH::roofer", "niche": "Roofing"}]
    assert calls == 2


def test_niche_proposal_batching_stays_inside_the_three_call_operation_budget():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        payload = json.loads(request.content)
        instruction = json.loads(payload["messages"][1]["content"])
        proposals = [
            {"id": item["id"], "niche": f"Niche {item['id']}"}
            for item in instruction["segments"]
        ]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": json.dumps({"proposals": proposals})}}
                ]
            },
        )

    provider = OpenRouterGenerationProvider(
        generation_settings(),
        transport=httpx.MockTransport(handler),
    )
    segments = [
        {"id": f"OH::trade-{index}", "keyword": f"trade {index}", "state": "OH"}
        for index in range(100)
    ]
    proposals = provider.propose_niches(segments)

    assert len(proposals) == 100
    assert calls == 3


def test_exclusion_metrics_distinguish_active_winner_and_imported_history(session):
    now = datetime.now(timezone.utc)
    session.add_all(
        [
            KeywordHistory(
                term="legacy roofer",
                origin="legacy_keyword_history",
                first_seen_at=now,
                last_seen_at=now,
            ),
            KeywordHistory(
                term="plumbers",
                origin="legacy_enqueue_log",
                first_seen_at=now,
                last_seen_at=now,
            ),
        ]
    )
    session.flush()
    history = list(session.scalars(select(KeywordHistory.term)))

    values, metrics = exclusion_metrics(
        active=["Plumbers", "Electricians"],
        winners=["plumbers", "Roof Repair"],
        history=history,
    )

    assert {value.casefold() for value in values} == {
        "plumbers",
        "electricians",
        "roof repair",
        "legacy roofer",
    }
    assert metrics == {
        "activeCount": 2,
        "winnerCount": 2,
        "historyCount": 2,
        "uniqueCount": 4,
    }


def test_drafts_expire_after_24_hours_accept_once_and_purge_after_90_days(session):
    administrator_id = uuid.uuid4()
    now = datetime(2026, 7, 31, 12, tzinfo=timezone.utc)
    result = KeywordGenerationResult(
        terms=candidates(1, 25),
        excluded_count=3,
        candidate_metrics={"attemptCount": 1},
    )
    draft = create_generation_draft(
        session,
        administrator_id=administrator_id,
        mode="broad",
        seed_keyword=None,
        model="test/model",
        result=result,
        exclusion_metrics={"uniqueCount": 4},
        now=now,
    )
    assert valid_generation_draft(
        session,
        draft.id,
        administrator_id=administrator_id,
        now=now + timedelta(hours=23, minutes=59),
    ) is draft
    assert valid_generation_draft(
        session,
        draft.id,
        administrator_id=administrator_id,
        now=now + timedelta(hours=24),
    ) is None

    accept_generation_draft(draft, now=now + timedelta(hours=1))
    assert valid_generation_draft(
        session,
        draft.id,
        administrator_id=administrator_id,
        now=now + timedelta(hours=2),
    ) is None
    assert purge_generation_drafts(
        session,
        now=now + timedelta(days=90),
    ) == 0
    assert purge_generation_drafts(
        session,
        now=now + timedelta(days=90, seconds=1),
    ) == 1
    assert session.scalar(select(KeywordGenerationDraftRecord)) is None


def test_generation_lock_uses_postgres_try_lock_and_non_postgres_is_noop():
    statement = None

    class PostgresSession:
        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

        def scalar(self, value):
            nonlocal statement
            statement = value
            return False

    assert try_generation_lock(PostgresSession()) is False
    assert "pg_try_advisory_xact_lock" in str(statement)

    class SQLiteSession:
        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))

        def scalar(self, _value):
            raise AssertionError("Non-PostgreSQL generation locking must be a no-op")

    assert try_generation_lock(SQLiteSession()) is True
