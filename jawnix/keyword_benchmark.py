"""Haiku prompt benchmark with a persistent budget around every paid request."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import httpx

from .config import Settings
from .keyword_generation import (
    KEYWORD_INDUSTRY_FOCI,
    GenerationErrorCode,
    KeywordGenerationError,
    OpenRouterGenerationProvider,
    generation_error,
)

MODEL = "claude-haiku-5-5"
# Includes the >100k tier, highest cache-write price, and US-only surcharge. No cache discount
# or provider routing assumption is used to authorize a request.
INPUT_CEILING = Decimal("1.10")
OUTPUT_CEILING = Decimal("2.75")
MILLION = Decimal(1000000)


class BenchmarkBudgetError(RuntimeError):
    pass


def money(value) -> Decimal:
    if isinstance(value, bool):
        raise BenchmarkBudgetError("Invalid monetary amount")
    try:
        amount = Decimal(str(value))
    except Exception as error:
        raise BenchmarkBudgetError("Missing or invalid request cost") from error
    if not amount.is_finite() or amount < 0:
        raise BenchmarkBudgetError("Invalid monetary amount")
    return amount


def save_json(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.chmod(0o600)
    os.replace(temporary, path)


def anthropic_cost(usage: dict) -> Decimal:
    counts = {}
    for field in [
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    ]:
        value = usage.get(field, 0) if field.startswith("cache_") else usage.get(field)
        if type(value) is not int or value < 0:
            raise BenchmarkBudgetError(
                "Anthropic token accounting is missing or invalid"
            )
        counts[field] = value
    total_input = (
        counts["input_tokens"]
        + counts["cache_creation_input_tokens"]
        + counts["cache_read_input_tokens"]
    )
    multiplier = Decimal(5) if total_input > 100000 else Decimal(1)
    geo = usage.get("inference_geo", "global")
    if geo not in ("global", "us"):
        raise BenchmarkBudgetError("Anthropic inference geography is unknown")
    if geo == "us":
        multiplier *= Decimal("1.1")
    creation = usage.get("cache_creation") or {}
    short = creation.get(
        "ephemeral_5m_input_tokens", counts["cache_creation_input_tokens"]
    )
    long = creation.get("ephemeral_1h_input_tokens", 0)
    if (
        type(short) is not int
        or type(long) is not int
        or min(short, long) < 0
        or short + long != counts["cache_creation_input_tokens"]
    ):
        raise BenchmarkBudgetError("Anthropic cache-write accounting is inconsistent")
    return (
        multiplier
        * (
            counts["input_tokens"] * Decimal("0.10")
            + counts["output_tokens"] * Decimal("0.50")
            + short * Decimal("0.125")
            + long * Decimal("0.20")
            + counts["cache_read_input_tokens"] * Decimal("0.01")
        )
        / MILLION
    )


class AnthropicHaikuProvider(OpenRouterGenerationProvider):
    """Reuse the existing prompt/filter/retry policy, with native Anthropic HTTP."""

    def __init__(
        self, key: str, *, transport=None, prompt_history_limit=None, workspace_id=None
    ):
        super().__init__(
            Settings(_env_file=None, OPENROUTER_KEYWORD_FALLBACK_MODELS=""),
            transport=transport,
            prompt_history_limit=prompt_history_limit,
        )
        self._native_key = key
        self._headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
        if workspace_id:
            self._headers["anthropic-workspace-id"] = workspace_id

    @property
    def model(self):
        return MODEL

    def _api_key(self):
        return self._native_key

    def _request(
        self,
        *,
        messages,
        response_schema,
        schema_name,
        max_tokens,
        temperature,
        deadline,
        model=None,
    ):
        if schema_name != "keyword_candidates" or model != MODEL:
            raise BenchmarkBudgetError(
                "Benchmark only supports Haiku keyword candidates"
            )
        started = time.monotonic()
        self._last_request_metrics = {}
        requested = response_schema["properties"]["keywords"]["maxItems"]
        # Native structured outputs do not support all string/array bounds.
        # The existing parser and full-history validator still enforce them.
        schema = {
            "type": "object",
            "properties": {
                "keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": f"Between 1 and {requested} categories, each 2 to 60 characters",
                }
            },
            "required": ["keywords"],
            "additionalProperties": False,
        }
        payload = {
            "model": MODEL,
            "max_tokens": max_tokens,
            "stream": False,
            "thinking": {"type": "disabled"},
            "service_tier": "standard_only",
            "system": "\n\n".join(
                m["content"] for m in messages if m["role"] == "system"
            ),
            "messages": [m for m in messages if m["role"] != "system"],
            "output_config": {
                "effort": "low",
                "format": {"type": "json_schema", "schema": schema},
            },
        }
        try:
            with httpx.Client(
                timeout=self._remaining(deadline), transport=self._transport
            ) as client:
                response = client.post(
                    "https://api.anthropic.com/v1/messages",
                    json=payload,
                    headers=self._headers,
                )
        except httpx.TimeoutException as error:
            raise generation_error(GenerationErrorCode.TIMEOUT) from error
        except httpx.HTTPError as error:
            raise generation_error(GenerationErrorCode.PROVIDER_UNREACHABLE) from error
        if response.status_code >= 400:
            code = {
                401: GenerationErrorCode.INVALID_API_KEY,
                403: GenerationErrorCode.MODEL_FORBIDDEN,
                429: GenerationErrorCode.RATE_LIMITED,
                529: GenerationErrorCode.PROVIDER_UNAVAILABLE,
            }.get(response.status_code, GenerationErrorCode.PROVIDER_REJECTED)
            raise generation_error(code)
        data = response.json()
        usage = data["usage"]
        self._last_request_metrics = {
            "latencyMs": round((time.monotonic() - started) * 1000),
            "inputUnits": sum(
                usage.get(field, 0)
                for field in [
                    "input_tokens",
                    "cache_creation_input_tokens",
                    "cache_read_input_tokens",
                ]
            ),
            "outputUnits": usage["output_tokens"],
            "cost": float(anthropic_cost(usage)),
        }
        self._remaining(deadline)
        if data["stop_reason"] == "max_tokens":
            raise generation_error(GenerationErrorCode.TRUNCATED)
        return "".join(
            block["text"] for block in data["content"] if block["type"] == "text"
        )


class BenchmarkBudget:
    """Reserve before sending, meter token usage, retain uncertain charges."""

    def __init__(self, path: Path, cap: Decimal):
        self.path = path
        self.cap = money(cap)
        if self.cap == 0:
            raise BenchmarkBudgetError("Benchmark cap must be positive")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.locked() as state:
            if money(state["cap_usd"]) != self.cap:
                raise BenchmarkBudgetError(
                    "An existing benchmark's cap cannot be changed"
                )

    @contextmanager
    def locked(self):
        with self.path.with_suffix(".lock").open("a") as guard:
            os.chmod(guard.name, 0o600)
            fcntl.flock(guard, fcntl.LOCK_EX)
            state = (
                json.loads(self.path.read_text())
                if self.path.exists()
                else {"cap_usd": str(self.cap), "blocked_reason": None, "calls": []}
            )
            yield state
            save_json(self.path, state)

    @staticmethod
    def committed(state):
        return sum(
            (
                money(
                    call["metered_usd"]
                    if call["metered_usd"] is not None
                    else call["reserved_usd"]
                )
                for call in state["calls"]
            ),
            Decimal(0),
        )

    def reserve(self, amount: Decimal, *, trial: str, payload_sha256: str) -> str:
        amount = money(amount)
        with self.locked() as state:
            if state["blocked_reason"]:
                raise BenchmarkBudgetError(state["blocked_reason"])
            if self.committed(state) + amount > self.cap:
                raise BenchmarkBudgetError(
                    "Benchmark spending cap reached before the next request"
                )
            request_id = uuid.uuid4().hex
            state["calls"].append(
                {
                    "id": request_id,
                    "trial": trial,
                    "reserved_usd": str(amount),
                    "metered_usd": None,
                    "status": "pending",
                    "payload_sha256": payload_sha256,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            return request_id

    def settle(self, request_id: str, *, cost: Decimal | None, metadata: dict):
        with self.locked() as state:
            call = next(c for c in state["calls"] if c["id"] == request_id)
            if call["status"] != "pending":
                raise BenchmarkBudgetError("Request accounting was already finalized")
            call.update(metadata)
            if cost is None:
                call["status"] = "uncertain"
                state["blocked_reason"] = (
                    "Request cost is unknown; further spending is blocked"
                )
            else:
                cost = money(cost)
                call["metered_usd"] = str(cost)
                call["status"] = "settled"
                if cost > money(call["reserved_usd"]):
                    state["blocked_reason"] = (
                        "Metered cost exceeded its reserved upper bound"
                    )
            blocked = state["blocked_reason"]
        if blocked:
            raise BenchmarkBudgetError(blocked)

    def summary(self):
        with self.locked() as state:
            charged = sum(
                (
                    money(c["metered_usd"])
                    for c in state["calls"]
                    if c["metered_usd"] is not None
                ),
                Decimal(0),
            )
            committed = self.committed(state)
            return {
                "cap_usd": str(self.cap),
                "metered_usd": str(charged),
                "held_usd": str(committed - charged),
                "remaining_usd": str(self.cap - committed),
                "requests": len(state["calls"]),
                "blocked_reason": state["blocked_reason"],
            }


class BudgetedTransport(httpx.BaseTransport):
    """Enforce the cap at the HTTP seam, including the provider's internal retries."""

    def __init__(self, budget: BenchmarkBudget, inner: httpx.BaseTransport):
        self.budget = budget
        self.inner = inner
        self.trial = "unassigned"

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if (
            request.method != "POST"
            or request.url.host != "api.anthropic.com"
            or request.url.path != "/v1/messages"
        ):
            raise BenchmarkBudgetError(
                "Benchmark only permits native Anthropic Messages"
            )
        payload = json.loads(request.read())
        if (
            payload.get("model") != MODEL
            or payload.get("stream") is not False
            or payload.get("thinking") != {"type": "disabled"}
            or payload.get("service_tier") != "standard_only"
            or payload.get("plugins")
            or payload.get("models")
            or payload.get("tools")
            or payload.get("cache_control")
        ):
            raise BenchmarkBudgetError("Unexpected benchmark model or paid feature")
        maximum_output = payload.get("max_tokens")
        if type(maximum_output) is not int or not 1 <= maximum_output <= 800:
            raise BenchmarkBudgetError("Benchmark requires an output-token ceiling")
        content = json.dumps(payload, ensure_ascii=True).encode()
        # UTF-8/escaped bytes conservatively bound text tokens, with additional
        # allowance for chat framing and schema scaffolding. Use the higher tier
        # for every reservation; do not assume caching discounts.
        maximum_input = len(content) + 8192
        if maximum_input + maximum_output > 1000000:
            raise BenchmarkBudgetError(
                "Prompt cannot be bounded inside Haiku's context window"
            )
        reservation = (
            maximum_input * INPUT_CEILING + maximum_output * OUTPUT_CEILING
        ) / MILLION
        request_id = self.budget.reserve(
            reservation,
            trial=self.trial,
            payload_sha256=hashlib.sha256(content).hexdigest(),
        )
        headers = dict(request.headers)
        headers.pop("content-length", None)
        outgoing = httpx.Request(
            request.method,
            request.url,
            headers=headers,
            content=content,
            extensions=request.extensions,
        )
        try:
            response = self.inner.handle_request(outgoing)
            response.read()
            data = json.loads(response.content, parse_float=Decimal)
            usage = data.get("usage") or {}
            cost = anthropic_cost(usage)
            metadata = {
                "http_status": response.status_code,
                "generation_id": data.get("id"),
                "model": data.get("model"),
                "input_tokens": sum(
                    usage.get(field, 0)
                    for field in [
                        "input_tokens",
                        "cache_creation_input_tokens",
                        "cache_read_input_tokens",
                    ]
                ),
                "output_tokens": usage.get("output_tokens"),
                "prompt_bytes": len(content),
                "maximum_input_tokens": maximum_input,
                "inference_geo": usage.get("inference_geo", "global"),
            }
        except Exception:
            self.budget.settle(
                request_id,
                cost=None,
                metadata={"transport_or_accounting_failure": True},
            )
            raise
        self.budget.settle(request_id, cost=cost, metadata=metadata)
        return response


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exclusions-file", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cap-usd", type=money, required=True)
    parser.add_argument("--api-key-file", type=Path)
    parser.add_argument(
        "--workspace-id", help="Required for multi-workspace Anthropic keys"
    )
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    args.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (args.output_dir / "runner.lock").open("a") as guard:
        os.chmod(guard.name, 0o600)
        try:
            fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise BenchmarkBudgetError(
                "This benchmark already has an active runner"
            ) from error
        run_benchmark(args)


def summarize(results: dict, budget: BenchmarkBudget):
    with budget.locked() as state:
        calls = list(state["calls"])
    variants = {}
    for variant in ["full", "compact"]:
        paid = [call for call in calls if call["trial"].endswith(":" + variant)]
        trials = [trial for trial in results["trials"] if trial["variant"] == variant]
        accepted = {term.casefold() for trial in trials for term in trial["terms"]}
        cost = sum(
            (
                money(call["metered_usd"])
                for call in paid
                if call["metered_usd"] is not None
            ),
            Decimal(0),
        )
        variants[variant] = {
            "completed_trials": len(trials),
            "requests": len(paid),
            "accepted_unique_keywords": len(accepted),
            "metered_usd": str(cost),
            "cost_per_accepted_keyword_usd": str(cost / len(accepted))
            if accepted
            else None,
            "input_tokens": sum(call.get("input_tokens") or 0 for call in paid),
            "output_tokens": sum(call.get("output_tokens") or 0 for call in paid),
            "rejected_duplicates": sum(
                trial["metrics"].get("rejectionReasons", {}).get("duplicate", 0)
                for trial in trials
            ),
            "seconds": round(sum(trial["seconds"] for trial in trials), 3),
        }
    return variants


def run_benchmark(args):
    snapshot = json.loads(args.exclusions_file.read_text())
    history = snapshot["keywords"]
    if not history or not all(isinstance(term, str) for term in history):
        raise BenchmarkBudgetError("A complete keyword-history snapshot is required")
    args.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    manifest = {
        "provider": "anthropic",
        "model": MODEL,
        "cap_usd": str(args.cap_usd),
        "cost_basis": "Anthropic public token prices, including context and cache tiers",
        "snapshot_sha256": hashlib.sha256(
            args.exclusions_file.read_bytes()
        ).hexdigest(),
        "history_count": len(history),
        "foci": list(KEYWORD_INDUSTRY_FOCI),
        "variants": {"full": None, "compact": 96},
        "production_activation": False,
    }
    if args.workspace_id:
        manifest["workspace_id"] = args.workspace_id
    budget = BenchmarkBudget(args.output_dir / "budget.json", args.cap_usd)
    manifest_path = args.output_dir / "manifest.json"
    if manifest_path.exists():
        existing = json.loads(manifest_path.read_text())
        # A dry run can precede workspace selection. Freeze routing once any
        # request has been authorized, including an uncertain request.
        without_workspace = {k: v for k, v in manifest.items() if k != "workspace_id"}
        if existing != manifest and not (
            existing == without_workspace and budget.summary()["requests"] == 0
        ):
            raise BenchmarkBudgetError("Existing benchmark inputs or cap differ")
    save_json(manifest_path, manifest)
    if not args.execute:
        previews = {}
        for variant, limit in manifest["variants"].items():
            provider = AnthropicHaikuProvider("", prompt_history_limit=limit)
            messages = provider._keyword_messages(
                mode="broad",
                excluded=history,
                seed_keyword=None,
                count=10,
                accepted=[],
                rejected=[],
                previous_error=None,
                industry_focus=KEYWORD_INDUSTRY_FOCI[0],
            )
            previews[variant] = {
                "prompt_bytes": len(json.dumps(messages).encode()),
                "visible_exclusions": len(
                    json.loads(messages[1]["content"])["excluded_keywords"]
                ),
            }
        result = {
            "mode": "dry_run",
            "manifest": manifest,
            "prompts": previews,
            "budget": budget.summary(),
        }
        save_json(args.output_dir / "dry-run.json", result)
        print(json.dumps(result, indent=2))
        return
    if args.api_key_file is None:
        raise BenchmarkBudgetError("Live benchmark requires an API-key file")
    key = args.api_key_file.read_text().strip()
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    if args.workspace_id:
        headers["anthropic-workspace-id"] = args.workspace_id
    with httpx.Client(timeout=20) as client:
        catalog = client.get(
            "https://api.anthropic.com/v1/models/" + MODEL,
            headers=headers,
        )
        if catalog.status_code != 200:
            raise BenchmarkBudgetError(
                "Anthropic key/model verification failed; no model request was sent"
            )
    inner = httpx.HTTPTransport(retries=0)
    transport = BudgetedTransport(budget, inner)
    results_path = args.output_dir / "results.json"
    results = (
        json.loads(results_path.read_text())
        if results_path.exists()
        else {"trials": [], "status": "running"}
    )
    try:
        for focus_index, focus in enumerate(KEYWORD_INDUSTRY_FOCI):
            order = ["full", "compact"] if focus_index % 2 == 0 else ["compact", "full"]
            for variant in order:
                trial_id = f"{focus_index}:{variant}"
                if any(trial["id"] == trial_id for trial in results["trials"]):
                    continue
                prior = [
                    term
                    for trial in results["trials"]
                    if trial["variant"] == variant
                    for term in trial["terms"]
                ]
                transport.trial = trial_id
                provider = AnthropicHaikuProvider(
                    key,
                    transport=transport,
                    prompt_history_limit=manifest["variants"][variant],
                    workspace_id=args.workspace_id,
                )
                start = time.monotonic()
                try:
                    generation = provider.generate_keywords(
                        mode="broad",
                        excluded_keywords=[*history, *prior],
                        count=10,
                        industry_focus=focus,
                    )
                    terms, metrics, error = (
                        generation.terms,
                        generation.candidate_metrics,
                        None,
                    )
                except KeywordGenerationError as failure:
                    terms, metrics, error = (
                        failure.partial_terms,
                        failure.metrics,
                        failure.code.value,
                    )
                results["trials"].append(
                    {
                        "id": trial_id,
                        "variant": variant,
                        "focus": focus,
                        "terms": terms,
                        "accepted": len(terms),
                        "metrics": metrics,
                        "error": error,
                        "seconds": round(time.monotonic() - start, 3),
                    }
                )
                results["budget"] = budget.summary()
                save_json(results_path, results)
                print(
                    json.dumps(
                        {
                            "trial": trial_id,
                            "accepted": len(terms),
                            "budget": results["budget"],
                        }
                    ),
                    flush=True,
                )
        results["status"] = "complete"
    except BenchmarkBudgetError as error:
        results.update(status="stopped", reason=str(error))
    finally:
        inner.close()
        results["budget"] = budget.summary()
        results["summary"] = summarize(results, budget)
        save_json(results_path, results)
    print(
        json.dumps(
            {
                "status": results["status"],
                "trials": len(results["trials"]),
                "budget": results["budget"],
            }
        )
    )


if __name__ == "__main__":
    main()
