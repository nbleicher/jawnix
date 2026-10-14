from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import json

import httpx
import pytest

from jawnix.keyword_benchmark import (
    BenchmarkBudget,
    BenchmarkBudgetError,
    BudgetedTransport,
    MODEL,
    AnthropicHaikuProvider,
    anthropic_cost,
)
import jawnix.keyword_benchmark as benchmark


def test_budget_reservations_are_atomic_and_survive_restart(tmp_path):
    path = tmp_path / "budget.json"
    budget = BenchmarkBudget(path, Decimal("5"))

    def reserve(_):
        try:
            return budget.reserve(Decimal("3"), trial="test", payload_sha256="test")
        except BenchmarkBudgetError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(reserve, range(2)))
    assert len([request for request in ids if request]) == 1
    restarted = BenchmarkBudget(path, Decimal("5"))
    assert restarted.summary()["held_usd"] == "3"
    with pytest.raises(BenchmarkBudgetError, match="cap reached"):
        restarted.reserve(Decimal("3"), trial="test", payload_sha256="test")
    with pytest.raises(BenchmarkBudgetError, match="cannot be changed"):
        BenchmarkBudget(path, Decimal("6"))


def test_reported_cost_releases_unused_reservation(tmp_path):
    budget = BenchmarkBudget(tmp_path / "budget.json", Decimal("5"))
    request = budget.reserve(Decimal("4"), trial="test", payload_sha256="test")
    budget.settle(request, cost=Decimal("1.25"), metadata={})
    assert budget.summary()["metered_usd"] == "1.25"
    assert budget.summary()["held_usd"] == "0.00"
    assert budget.summary()["remaining_usd"] == "3.75"
    budget.reserve(Decimal("3.75"), trial="test", payload_sha256="test")
    assert budget.summary()["remaining_usd"] == "0.00"


def test_conservative_resumption_keeps_the_entire_unknown_charge_in_the_cap(tmp_path):
    budget = BenchmarkBudget(tmp_path / "budget.json", Decimal("1"))
    request = budget.reserve(Decimal("0.7"), trial="test", payload_sha256="test")
    with pytest.raises(BenchmarkBudgetError):
        budget.settle(request, cost=None, metadata={})
    budget.account_uncertain_at_upper_bound(
        request, reason="The request's token and price ceilings are bounded"
    )
    assert budget.summary()["held_usd"] == "0.7"
    assert budget.summary()["metered_usd"] == "0"
    budget.reserve(Decimal("0.3"), trial="test", payload_sha256="test")
    with pytest.raises(BenchmarkBudgetError, match="cap reached"):
        budget.reserve(Decimal("0.001"), trial="test", payload_sha256="test")


def provider(transport):
    return AnthropicHaikuProvider(
        "must-not-be-recorded", transport=transport, prompt_history_limit=96
    )


def completion(terms, cost):
    inputs = int((Decimal(str(cost)) - Decimal("0.00005")) * 10000000)
    return httpx.Response(
        200,
        json={
            "id": "generation-test",
            "model": MODEL,
            "stop_reason": "end_turn",
            "usage": {"input_tokens": inputs, "output_tokens": 100},
            "content": [{"type": "text", "text": json.dumps({"keywords": terms})}],
        },
    )


def test_cap_blocks_outgoing_http_before_first_request(tmp_path):
    calls = []
    budget = BenchmarkBudget(tmp_path / "budget.json", Decimal("0.00001"))
    transport = BudgetedTransport(
        budget, httpx.MockTransport(lambda r: calls.append(r))
    )
    with pytest.raises(BenchmarkBudgetError, match="cap reached"):
        provider(transport).generate_keywords(
            mode="broad", excluded_keywords=[], count=1
        )
    assert calls == []
    assert budget.summary()["requests"] == 0


def test_internal_duplicate_retries_are_charged_and_capped(tmp_path):
    calls = []

    def handler(request):
        payload = json.loads(request.content)
        assert request.url.host == "api.anthropic.com"
        assert request.headers["x-api-key"] == "must-not-be-recorded"
        assert "provider" not in payload
        assert payload["max_tokens"] == 800
        assert payload["thinking"]["type"] == "disabled"
        assert payload["service_tier"] == "standard_only"
        calls.append(request)
        return completion(["Plumbers"], 0.001)

    path = tmp_path / "budget.json"
    budget = BenchmarkBudget(path, Decimal("0.016"))
    transport = BudgetedTransport(budget, httpx.MockTransport(handler))
    with pytest.raises(BenchmarkBudgetError, match="cap reached"):
        provider(transport).generate_keywords(
            mode="broad", excluded_keywords=["Plumbers"], count=1
        )
    assert 1 <= len(calls) < 5
    assert Decimal(budget.summary()["metered_usd"]) == len(calls) * Decimal("0.001")
    assert Decimal(budget.summary()["metered_usd"]) <= Decimal("0.016")
    assert "must-not-be-recorded" not in path.read_text()


@pytest.mark.parametrize("failure", ["timeout", "missing_usage", "malformed_json"])
def test_uncertain_charge_is_retained_and_blocks_restart(tmp_path, failure):
    calls = []

    def handler(request):
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("timeout", request=request)
        if failure == "malformed_json":
            return httpx.Response(200, text="broken JSON")
        return httpx.Response(200, json={"choices": []})

    path = tmp_path / "budget.json"
    budget = BenchmarkBudget(path, Decimal("5"))
    with pytest.raises(BenchmarkBudgetError, match="unknown"):
        provider(
            BudgetedTransport(budget, httpx.MockTransport(handler))
        ).generate_keywords(mode="broad", excluded_keywords=[], count=1)
    assert len(calls) == 1
    restarted = BenchmarkBudget(path, Decimal("5"))
    assert Decimal(restarted.summary()["held_usd"]) > 0
    with pytest.raises(BenchmarkBudgetError, match="unknown"):
        provider(
            BudgetedTransport(restarted, httpx.MockTransport(handler))
        ).generate_keywords(mode="broad", excluded_keywords=[], count=1)
    assert len(calls) == 1


def test_transport_rejects_an_unexpected_model_before_spending(tmp_path):
    calls = []
    budget = BenchmarkBudget(tmp_path / "budget.json", Decimal("5"))
    transport = BudgetedTransport(
        budget, httpx.MockTransport(lambda r: calls.append(r))
    )
    request = httpx.Request(
        "POST",
        "https://api.anthropic.com/v1/messages",
        json={
            "model": "google/gemini-3-flash-preview",
            "stream": False,
            "thinking": {"type": "disabled"},
            "service_tier": "standard_only",
            "max_tokens": 800,
        },
    )
    with pytest.raises(BenchmarkBudgetError, match="Unexpected"):
        transport.handle_request(request)
    assert calls == []


def test_successful_requests_reuse_the_guarded_transport(tmp_path):
    calls = []
    budget = BenchmarkBudget(tmp_path / "budget.json", Decimal("5"))

    def handler(request):
        calls.append(request)
        return completion(["Vintage camera repair"], 0.001)

    transport = BudgetedTransport(budget, httpx.MockTransport(handler))
    for _ in range(2):
        result = provider(transport).generate_keywords(
            mode="broad", excluded_keywords=[], count=1
        )
        assert result.terms == ["Vintage camera repair"]
    assert len(calls) == 2
    assert budget.summary()["metered_usd"] == "0.002"


def test_cli_preflight_invalid_key_sends_no_model_request(tmp_path, monkeypatch):
    import sys

    snapshot = tmp_path / "history.json"
    snapshot.write_text(json.dumps({"keywords": ["Old category"]}))
    key = tmp_path / "key.txt"
    key.write_text("must-not-be-recorded")
    calls = []

    def handler(request):
        calls.append(request)
        assert request.method == "GET"
        assert request.url.path == "/v1/models/" + MODEL
        return httpx.Response(401, json={"error": {"type": "authentication_error"}})

    original = httpx.Client
    monkeypatch.setattr(
        benchmark.httpx,
        "Client",
        lambda **kw: original(transport=httpx.MockTransport(handler), **kw),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "benchmark",
            "--exclusions-file",
            str(snapshot),
            "--output-dir",
            str(tmp_path / "result"),
            "--cap-usd",
            "5",
            "--execute",
            "--api-key-file",
            str(key),
        ],
    )
    with pytest.raises(BenchmarkBudgetError, match="verification failed"):
        benchmark.main()
    assert len(calls) == 1
    assert (
        BenchmarkBudget(tmp_path / "result/budget.json", Decimal("5")).summary()[
            "requests"
        ]
        == 0
    )


@pytest.mark.parametrize("workspace_id", [None, "wrkspc_test123"])
def test_cli_runs_matched_variants_with_a_shared_budget(
    tmp_path, monkeypatch, workspace_id
):
    import sys
    from hashlib import sha256

    snapshot = tmp_path / "history.json"
    snapshot.write_text(json.dumps({"keywords": ["Old category"]}))
    key = tmp_path / "key.txt"
    key.write_text("must-not-be-recorded")
    outgoing = []

    def handler(request):
        assert request.headers.get("anthropic-workspace-id") == workspace_id
        if request.url.path == "/v1/models/" + MODEL:
            return httpx.Response(200, json={"id": MODEL})
        assert request.method == "POST"
        payload = json.loads(request.content)
        assert payload["service_tier"] == "standard_only"
        assert payload["output_config"]["format"]["type"] == "json_schema"
        assert (
            "minLength"
            not in payload["output_config"]["format"]["schema"]["properties"][
                "keywords"
            ]["items"]
        )
        outgoing.append(request)
        terms = [
            "Trade " + sha256(f"{len(outgoing)}:{i}".encode()).hexdigest()[:10]
            for i in range(10)
        ]
        return completion(terms, 0.001)

    original = httpx.Client

    def client(**kwargs):
        kwargs.setdefault("transport", httpx.MockTransport(handler))
        return original(**kwargs)

    monkeypatch.setattr(benchmark.httpx, "Client", client)
    monkeypatch.setattr(
        benchmark.httpx, "HTTPTransport", lambda **kw: httpx.MockTransport(handler)
    )
    monkeypatch.setattr(
        benchmark, "KEYWORD_INDUSTRY_FOCI", ("camera equipment", "food shops")
    )
    argv = [
        "benchmark",
        "--exclusions-file",
        str(snapshot),
        "--output-dir",
        str(tmp_path / "result"),
        "--cap-usd",
        "5",
        "--execute",
        "--api-key-file",
        str(key),
    ]
    if workspace_id:
        argv += ["--workspace-id", workspace_id]
    monkeypatch.setattr(
        sys,
        "argv",
        [
            argument
            for argument in argv
            if argument not in ("--execute", "--workspace-id", workspace_id)
        ],
    )
    benchmark.main()
    assert outgoing == []
    monkeypatch.setattr(sys, "argv", argv)
    benchmark.main()
    result = json.loads((tmp_path / "result/results.json").read_text())
    assert result["status"] == "complete"
    assert len(result["trials"]) == len(outgoing) == 4
    assert result["budget"]["metered_usd"] == "0.004"
    assert result["summary"]["full"]["accepted_unique_keywords"] == 20
    assert result["summary"]["compact"]["accepted_unique_keywords"] == 20
    assert result["summary"]["compact"]["metered_usd"] == "0.002"
    for filename in ["budget.json", "results.json", "manifest.json"]:
        assert (
            "must-not-be-recorded" not in (tmp_path / "result" / filename).read_text()
        )
    benchmark.main()
    assert len(outgoing) == 4
    changed = list(argv)
    if workspace_id:
        changed[changed.index(workspace_id)] = "wrkspc_changed123"
    else:
        changed += ["--workspace-id", "wrkspc_changed123"]
    monkeypatch.setattr(sys, "argv", changed)
    with pytest.raises(BenchmarkBudgetError, match="inputs or cap differ"):
        benchmark.main()
    assert len(outgoing) == 4


@pytest.mark.parametrize(
    "inputs,outputs,expected", [(100000, 1000, "0.0105"), (100001, 1000, "0.0525005")]
)
def test_native_usage_prices_the_correct_context_tier(inputs, outputs, expected):
    assert anthropic_cost(
        {"input_tokens": inputs, "output_tokens": outputs}
    ) == Decimal(expected)


def test_native_cache_counts_are_included_in_price_and_tier():
    usage = {
        "input_tokens": 1,
        "output_tokens": 100,
        "cache_creation_input_tokens": 100000,
        "cache_read_input_tokens": 1000,
        "cache_creation": {
            "ephemeral_5m_input_tokens": 50000,
            "ephemeral_1h_input_tokens": 50000,
        },
    }
    assert anthropic_cost(usage) == Decimal("0.0815505")


@pytest.mark.parametrize(
    "inputs,expected", [(100000, "0.01155"), (100001, "0.05775055")]
)
def test_native_us_inference_surcharge_is_included(inputs, expected):
    assert anthropic_cost(
        {"input_tokens": inputs, "output_tokens": 1000, "inference_geo": "us"}
    ) == Decimal(expected)


@pytest.mark.parametrize(
    "usage",
    [
        {},
        {"input_tokens": -1, "output_tokens": 1},
        {"input_tokens": True, "output_tokens": 1},
        {
            "input_tokens": 1,
            "output_tokens": 1,
            "cache_creation_input_tokens": 2,
            "cache_creation": {
                "ephemeral_5m_input_tokens": 1,
                "ephemeral_1h_input_tokens": 0,
            },
        },
    ],
)
def test_invalid_native_usage_cannot_release_a_reservation(usage):
    with pytest.raises(BenchmarkBudgetError):
        anthropic_cost(usage)
