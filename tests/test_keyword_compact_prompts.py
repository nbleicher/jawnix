"""Compact model context must retain complete local duplicate validation."""

import json

import httpx

from jawnix.config import Settings
from jawnix.keyword_generation import OpenRouterGenerationProvider


def test_large_history_is_bounded_in_prompt_but_hidden_duplicates_are_rejected():
    history = [f"Historic category {index:05d}" for index in range(12549)]
    requests = []
    omitted = []

    def handler(request):
        payload = json.loads(request.content)
        instruction = json.loads(payload["messages"][1]["content"])
        requests.append(payload)
        assert len(instruction["excluded_keywords"]) <= 96
        assert len(json.dumps(payload["messages"]).encode()) < 8500
        assert instruction["exclusion_history_count"] == len(history)
        hidden = next(
            term for term in history if term not in instruction["excluded_keywords"]
        )
        omitted.append(hidden)
        terms = (
            [hidden, "Vintage camera repair"]
            if len(requests) == 1
            else ["Darkroom equipment supply"]
        )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps({"keywords": terms})},
                    }
                ]
            },
        )

    provider = OpenRouterGenerationProvider(
        Settings(
            _env_file=None, OPENROUTER_API_KEY="test-key", OPENROUTER_MODEL="test/model"
        ),
        transport=httpx.MockTransport(handler),
        prompt_history_limit=96,
    )
    result = provider.generate_keywords(
        mode="broad",
        excluded_keywords=history,
        count=2,
        industry_focus="photography and camera equipment",
    )
    assert result.terms == ["Vintage camera repair", "Darkroom equipment supply"]
    assert result.candidate_metrics["rejectionReasons"]["duplicate"] == 1
    assert omitted[0] not in result.terms


def test_compact_history_prioritizes_focus_and_bounds_long_values():
    history = [f"Unrelated category {index:04d}" for index in range(1000)]
    history += ["Camera equipment rental", "Camera equipment supplier", "x" * 50000]
    provider = OpenRouterGenerationProvider(
        Settings(_env_file=None), prompt_history_limit=96
    )
    messages = provider._keyword_messages(
        mode="broad",
        excluded=history,
        seed_keyword=None,
        count=10,
        accepted=[],
        rejected=[],
        previous_error=None,
        industry_focus="camera equipment",
    )
    instruction = json.loads(messages[1]["content"])
    assert "Camera equipment rental" in instruction["excluded_keywords"]
    assert "Camera equipment supplier" in instruction["excluded_keywords"]
    assert len(instruction["excluded_keywords"]) <= 96
    assert len(json.dumps(messages).encode()) < 8500


def test_compact_retry_context_is_bounded_without_changing_full_history_mode():
    excluded = [f"Old category {index}" for index in range(200)]
    rejected = [
        {"candidate": "Rejected " + str(i), "reason": "duplicate"} for i in range(100)
    ]
    for limit, expected in [(None, 100), (96, 16)]:
        provider = OpenRouterGenerationProvider(
            Settings(_env_file=None), prompt_history_limit=limit
        )
        messages = provider._keyword_messages(
            mode="broad",
            excluded=excluded,
            seed_keyword=None,
            count=10,
            accepted=["Accepted category"],
            rejected=rejected,
            previous_error=None,
            industry_focus="camera equipment",
        )
        instruction = json.loads(messages[1]["content"])
        assert len(instruction["retry_context"]["rejected_candidates"]) == expected
        assert instruction["retry_context"]["accepted_keywords"] == [
            "Accepted category"
        ]
        if limit is None:
            assert instruction["excluded_keywords"] == excluded
