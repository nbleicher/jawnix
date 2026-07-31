import json
import hashlib
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

from app.keyword_generator import (
    GenerationError,
    KeywordGenerator,
    filter_candidates,
    is_near_duplicate,
    normalize_keyword,
    parse_candidate_content,
)


def settings(key="secret-test-key", timeout=45):
    return SimpleNamespace(
        openrouter_api_key=SecretStr(key),
        openrouter_model="deepseek/deepseek-v4-flash",
        openrouter_base_url="https://openrouter.test/api/v1",
        openrouter_timeout_secs=timeout,
    )


def test_legal_industry_keywords_are_hard_blocked():
    blocked = [
        "Attorney Referral Service", "Personal Injury Lawyer", "Law Firm",
        "Family Law Practice", "Legal Aid Clinic", "Paralegal Services",
        "Litigation Support", "Notary Public",
    ]
    result = filter_candidates(blocked + ["Lawn Care", "Bicycle Repair"], [])
    assert result.keywords == ["Lawn Care", "Bicycle Repair"]
    assert result.excluded_count == len(blocked)


def test_normalization_and_similarity_filtering():
    assert normalize_keyword("  1. Wedding Cakes  ") == "Wedding Cakes"
    assert is_near_duplicate("Wedding Cakes", "Wedding Cake")
    assert is_near_duplicate("Frame Services", "Frame Service")
    result = filter_candidates(
        ["Wedding Cakes", "HVAC Contractor", "HVAC Contractors", "x", "Bicycle Repair"],
        ["Wedding Cake"],
    )
    assert result.keywords == ["HVAC Contractor", "Bicycle Repair"]
    assert result.excluded_count == 3


@pytest.mark.parametrize("content", [
    {"keywords": ["Plumber", "Florist"]},
    ["Plumber", "Florist"],
    "```json\n{\"keywords\":[\"Plumber\",\"Florist\"]}\n```",
    "Here is the result: {\"keywords\":[\"Plumber\",\"Florist\"]}",
    [{"type": "text", "text": "{\"keywords\":[\"Plumber\",\"Florist\"]}"}],
])
def test_candidate_content_accepts_openrouter_response_shapes(content):
    assert parse_candidate_content(content) == ["Plumber", "Florist"]


@pytest.mark.asyncio
async def test_generation_replenishes_to_exactly_25(monkeypatch):
    generator = KeywordGenerator(settings())
    calls = []

    async def fake_request(mode, excluded, seed, count=40):
        calls.append(list(excluded))
        start = 1 if len(calls) == 1 else 30
        amount = 20 if len(calls) == 1 else 30
        return [f"Trade {hashlib.sha256(str(number).encode()).hexdigest()[:8]}" for number in range(start, start + amount)]

    monkeypatch.setattr(generator, "_request_candidates", fake_request)
    result = await generator.generate("broad", ["Already Used"])
    assert len(result.keywords) == 25
    assert len(set(map(str.casefold, result.keywords))) == 25
    assert len(calls) == 2
    assert set(result.keywords[:20]).issubset(set(calls[1]))


@pytest.mark.asyncio
@pytest.mark.parametrize("status,message", [
    (400, "rejected"), (401, "invalid or revoked"), (402, "insufficient credit"),
    (403, "cannot use"), (408, "timed out"), (429, "rate limiting"),
    (502, "invalid response"), (503, "temporarily unavailable"),
])
async def test_provider_errors_are_sanitized(monkeypatch, status, message):
    real_client = httpx.AsyncClient

    def handler(request):
        return httpx.Response(status, json={"error": {"message": "provider secret detail"}})

    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), timeout=kwargs.get("timeout")),
    )
    generator = KeywordGenerator(settings("must-not-appear"))
    with pytest.raises(GenerationError) as raised:
        await generator._request_candidates("broad", [], None)
    assert message in str(raised.value)
    assert "must-not-appear" not in str(raised.value)
    assert "provider secret detail" not in str(raised.value)


@pytest.mark.asyncio
async def test_provider_json_response(monkeypatch):
    real_client = httpx.AsyncClient
    captured = {}

    def handler(request):
        captured["authorization"] = request.headers["Authorization"]
        payload = json.loads(request.content)
        captured["payload"] = payload
        content = json.dumps({"keywords": [f"Category {index}" for index in range(40)]})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), timeout=kwargs.get("timeout")),
    )
    result = await KeywordGenerator(settings())._request_candidates("broad", ["Used"], None)
    assert len(result) == 40
    assert captured["authorization"] == "Bearer secret-test-key"
    assert captured["payload"]["model"] == "deepseek/deepseek-v4-flash"
    assert captured["payload"]["max_tokens"] == 1600
    assert captured["payload"]["reasoning"] == {"enabled": False, "exclude": True}
    assert captured["payload"]["response_format"]["type"] == "json_schema"


@pytest.mark.asyncio
async def test_truncated_provider_response_is_identified(monkeypatch):
    real_client = httpx.AsyncClient

    def handler(request):
        return httpx.Response(200, json={
            "choices": [{"finish_reason": "length", "message": {"content": ""}}],
        })

    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), timeout=kwargs.get("timeout")),
    )
    with pytest.raises(GenerationError, match="output limit"):
        await KeywordGenerator(settings())._request_candidates("broad", [], None)


@pytest.mark.asyncio
async def test_provider_timeout_is_sanitized(monkeypatch):
    real_client = httpx.AsyncClient

    def handler(request):
        raise httpx.ReadTimeout("secret timeout detail", request=request)

    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), timeout=kwargs.get("timeout")),
    )
    with pytest.raises(GenerationError, match="timed out") as raised:
        await KeywordGenerator(settings("hidden-key"))._request_candidates("broad", [], None)
    assert "hidden-key" not in str(raised.value)
    assert "secret timeout detail" not in str(raised.value)


@pytest.mark.asyncio
async def test_provider_has_total_request_timeout(monkeypatch):
    class SlowClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            import asyncio
            await asyncio.sleep(0.1)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: SlowClient())
    with pytest.raises(GenerationError, match="timed out"):
        await KeywordGenerator(settings(timeout=0.01))._request_candidates("broad", [], None)


@pytest.mark.asyncio
async def test_malformed_provider_response(monkeypatch):
    real_client = httpx.AsyncClient

    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "not-json"}}]})

    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), timeout=kwargs.get("timeout")),
    )
    with pytest.raises(GenerationError, match="malformed keyword data"):
        await KeywordGenerator(settings())._request_candidates("broad", [], None)
