import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from jawnix.anthropic_generation import KeywordSpendError
from jawnix.config import Settings
from jawnix.keyword_generation import (
    GenerationErrorCode,
    KeywordGenerationError,
    build_generation_provider,
)
from jawnix.keyword_spend import DatabaseKeywordBudget
from jawnix.models import KeywordGenerationCharge


def native_settings():
    return Settings(
        _env_file=None,
        JAWNIX_KEYWORD_GENERATION_PROVIDER="anthropic",
        ANTHROPIC_API_KEY="must-not-be-recorded",
        ANTHROPIC_WORKSPACE_ID="wrkspc_fixture",
    )


def native_response(terms):
    return httpx.Response(
        200,
        json={
            "model": "claude-haiku-5-5",
            "id": "request-fixture",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 1000, "output_tokens": 300},
            "content": [{"type": "text", "text": json.dumps({"keywords": terms})}],
        },
    )


def test_factory_uses_native_oversampling_and_subfamilies_with_full_local_history(
    session,
):
    factory = sessionmaker(session.get_bind(), expire_on_commit=False)
    terms = [
        "Trade " + hashlib.sha256(str(i).encode()).hexdigest()[:10] for i in range(30)
    ]
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.host == "api.anthropic.com"
        assert request.headers["anthropic-workspace-id"] == "wrkspc_fixture"
        data = json.loads(request.content)
        instruction = json.loads(data["messages"][0]["content"])
        assert instruction["candidate_count"] == 30
        assert len(instruction["industry_subfamilies"]) == 6
        assert instruction["max_candidates_per_subfamily"] == 5
        assert "no more than two" not in data["system"]
        assert data["max_tokens"] == 800
        assert "temperature" not in data
        assert "provider" not in data
        return native_response(terms)

    provider = build_generation_provider(
        native_settings(),
        session_factory=factory,
        transport=httpx.MockTransport(handler),
    )
    result = provider.generate_keywords(
        mode="broad", excluded_keywords=terms[:2], count=25
    )
    assert result.terms == terms[2:27]
    assert result.candidate_metrics["rejectionReasons"]["duplicate"] == 2
    assert len(calls) == 1
    session.expire_all()
    charge = session.scalar(select(KeywordGenerationCharge))
    assert charge.status == "settled" and charge.metered_usd == Decimal("0.00025")
    assert "must-not-be-recorded" not in json.dumps(charge.details)


def test_native_empty_array_is_a_focus_shortfall_not_a_malformed_provider_response(
    session,
):
    factory = sessionmaker(session.get_bind(), expire_on_commit=False)
    calls = []

    def handler(request):
        calls.append(request)
        return native_response([])

    provider = build_generation_provider(
        native_settings(),
        session_factory=factory,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(KeywordGenerationError) as failure:
        provider.generate_keywords(mode="broad", excluded_keywords=[], count=25)
    assert failure.value.code == GenerationErrorCode.NO_CANDIDATES
    assert len(calls) == 1


def test_native_surplus_response_keeps_valid_work_within_the_requested_goal(session):
    factory = sessionmaker(session.get_bind(), expire_on_commit=False)
    terms = [
        "Trade " + hashlib.sha256(f"surplus:{i}".encode()).hexdigest()[:10]
        for i in range(39)
    ]
    calls = []

    def handler(request):
        calls.append(request)
        return native_response(terms)

    provider = build_generation_provider(
        native_settings(),
        session_factory=factory,
        transport=httpx.MockTransport(handler),
    )
    result = provider.generate_keywords(mode="broad", excluded_keywords=[], count=25)
    assert result.terms == terms[:25]
    assert len(calls) == 1
    assert result.candidate_metrics["surplusCount"] == 14


def test_native_niche_classification_uses_the_same_persistent_budget(session):
    factory = sessionmaker(session.get_bind(), expire_on_commit=False)
    calls = []

    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        assert payload["max_tokens"] == 2000
        response = native_response([])
        data = response.json()
        data["content"][0]["text"] = json.dumps(
            {"proposals": [{"id": "segment-1", "niche": "Roofing"}]}
        )
        return httpx.Response(200, json=data)

    provider = build_generation_provider(
        native_settings(),
        session_factory=factory,
        transport=httpx.MockTransport(handler),
    )
    assert provider.propose_niches(
        [{"id": "segment-1", "keyword": "roofer", "state": "TN"}]
    ) == [{"id": "segment-1", "niche": "Roofing"}]
    session.expire_all()
    charge = session.scalar(select(KeywordGenerationCharge))
    assert charge.purpose == "source_niche_proposals" and charge.status == "settled"
    assert len(calls) == 1


def test_native_malformed_envelope_retries_without_discarding_the_charge(session):
    factory = sessionmaker(session.get_bind(), expire_on_commit=False)
    terms = [
        "Trade " + hashlib.sha256(f"envelope:{i}".encode()).hexdigest()[:10]
        for i in range(30)
    ]
    calls = []

    def handler(request):
        calls.append(request)
        response = native_response(terms)
        if len(calls) == 1:
            data = response.json()
            del data["content"]
            return httpx.Response(200, json=data)
        return response

    provider = build_generation_provider(
        native_settings(),
        session_factory=factory,
        transport=httpx.MockTransport(handler),
    )
    assert (
        provider.generate_keywords(mode="broad", excluded_keywords=[], count=25).terms
        == terms[:25]
    )
    session.expire_all()
    charges = list(session.scalars(select(KeywordGenerationCharge)))
    assert len(charges) == len(calls) == 2
    assert all(charge.metered_usd == Decimal("0.00025") for charge in charges)


def test_daily_cap_is_atomic_across_provider_instances_and_restart(session):
    factory = sessionmaker(session.get_bind(), expire_on_commit=False)
    clock = lambda: datetime(2026, 10, 13, 12, tzinfo=UTC)

    def reserve(_):
        try:
            return DatabaseKeywordBudget(factory, Decimal(1), clock=clock).reserve(
                Decimal("0.7"), trial="test", payload_sha256="hash"
            )
        except KeywordSpendError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        requests = list(pool.map(reserve, range(2)))
    assert len([r for r in requests if r]) == 1
    with pytest.raises(KeywordSpendError, match="Daily keyword spending limit"):
        DatabaseKeywordBudget(factory, Decimal(1), clock=clock).reserve(
            Decimal("0.4"), trial="test", payload_sha256="hash"
        )


def test_daily_cap_resets_at_the_configured_calendar_boundary(session):
    factory = sessionmaker(session.get_bind(), expire_on_commit=False)
    now = [datetime(2026, 10, 14, 3, 59, tzinfo=UTC)]
    budget = DatabaseKeywordBudget(
        factory, Decimal(1), timezone_name="America/New_York", clock=lambda: now[0]
    )
    budget.reserve(Decimal(1), trial="test", payload_sha256="hash")
    with pytest.raises(KeywordSpendError):
        budget.reserve(Decimal("0.1"), trial="test", payload_sha256="hash")
    now[0] += timedelta(minutes=2)
    budget.reserve(Decimal(1), trial="test", payload_sha256="hash")
    session.expire_all()
    assert len(list(session.scalars(select(KeywordGenerationCharge)))) == 2


def test_uncertain_charge_retains_full_reservation_before_a_retry(session):
    factory = sessionmaker(session.get_bind(), expire_on_commit=False)
    budget = DatabaseKeywordBudget(factory, Decimal(1))
    request = budget.reserve(Decimal("0.2"), trial="test", payload_sha256="hash")
    budget.settle(request, cost=None, metadata={})
    restarted = DatabaseKeywordBudget(factory, Decimal(1))
    restarted.reserve(Decimal("0.8"), trial="test", payload_sha256="hash")
    with pytest.raises(KeywordSpendError, match="Daily keyword spending limit"):
        restarted.reserve(Decimal("0.01"), trial="test", payload_sha256="hash")


def test_cap_blocks_native_http_before_any_request(session):
    factory = sessionmaker(session.get_bind(), expire_on_commit=False)
    calls = []
    config = native_settings().model_copy(
        update={"keyword_daily_spend_usd": Decimal("0.00001")}
    )
    provider = build_generation_provider(
        config,
        session_factory=factory,
        transport=httpx.MockTransport(lambda r: calls.append(r)),
    )
    with pytest.raises(KeywordGenerationError) as failure:
        provider.generate_keywords(mode="broad", excluded_keywords=[], count=25)
    assert failure.value.code == GenerationErrorCode.SPEND_BLOCKED
    assert calls == []


def test_native_provider_errors_are_metered_zero_and_preserve_error_type(session):
    factory = sessionmaker(session.get_bind(), expire_on_commit=False)
    provider = build_generation_provider(
        native_settings(),
        session_factory=factory,
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                401, json={"type": "error", "error": {"type": "authentication_error"}}
            )
        ),
    )
    with pytest.raises(KeywordGenerationError) as failure:
        provider.generate_keywords(mode="broad", excluded_keywords=[], count=25)
    assert failure.value.code == GenerationErrorCode.INVALID_API_KEY
    session.expire_all()
    charge = session.scalar(select(KeywordGenerationCharge))
    assert charge.status == "settled" and charge.metered_usd == 0
