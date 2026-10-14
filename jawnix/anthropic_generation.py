"""Native Haiku generation and token accounting shared by production and benchmarks."""

from __future__ import annotations

import hashlib
import json
import time
from decimal import Decimal

import httpx

from .config import Settings
from .keyword_generation import (
    GenerationErrorCode,
    OpenRouterGenerationProvider,
    generation_error,
)

MODEL = "claude-haiku-5-5"
INPUT_CEILING = Decimal("1.10")
OUTPUT_CEILING = Decimal("2.75")
MILLION = Decimal(1000000)


class KeywordSpendError(RuntimeError):
    pass


def money(value) -> Decimal:
    if isinstance(value, bool):
        raise KeywordSpendError("Invalid monetary amount")
    try:
        amount = Decimal(str(value))
    except Exception as error:
        raise KeywordSpendError("Missing or invalid request cost") from error
    if not amount.is_finite() or amount < 0:
        raise KeywordSpendError("Invalid monetary amount")
    return amount


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
            raise KeywordSpendError("Anthropic token accounting is missing or invalid")
        counts[field] = value
    total_input = (
        counts["input_tokens"]
        + counts["cache_creation_input_tokens"]
        + counts["cache_read_input_tokens"]
    )
    multiplier = Decimal(5) if total_input > 100000 else Decimal(1)
    geo = usage.get("inference_geo", "global")
    if geo not in ("global", "us"):
        raise KeywordSpendError("Anthropic inference geography is unknown")
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
        raise KeywordSpendError("Anthropic cache-write accounting is inconsistent")
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


class AnthropicBudgetTransport(httpx.BaseTransport):
    """Enforce the cap at the HTTP seam, including the provider's internal retries."""

    def __init__(self, budget, inner: httpx.BaseTransport, *, max_output_tokens=800):
        self.budget = budget
        self.inner = inner
        self.trial = "unassigned"
        self.max_output_tokens = max_output_tokens

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if (
            request.method != "POST"
            or request.url.host != "api.anthropic.com"
            or request.url.path != "/v1/messages"
        ):
            raise KeywordSpendError("Benchmark only permits native Anthropic Messages")
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
            raise KeywordSpendError("Unexpected benchmark model or paid feature")
        maximum_output = payload.get("max_tokens")
        if (
            type(maximum_output) is not int
            or not 1 <= maximum_output <= self.max_output_tokens
        ):
            raise KeywordSpendError("Benchmark requires an output-token ceiling")
        content = json.dumps(payload, ensure_ascii=True).encode()
        # UTF-8/escaped bytes conservatively bound text tokens, with additional
        # allowance for chat framing and schema scaffolding. Use the higher tier
        # for every reservation; do not assume caching discounts.
        maximum_input = len(content) + 8192
        if maximum_input + maximum_output > 1000000:
            raise KeywordSpendError(
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
        response = None
        try:
            response = self.inner.handle_request(outgoing)
            response.read()
            data = json.loads(response.content, parse_float=Decimal)
            usage = data.get("usage") or {}
            cost = (
                Decimal(0)
                if response.status_code >= 400
                and data.get("type") == "error"
                and isinstance(data.get("error"), dict)
                and not usage
                else anthropic_cost(usage)
            )
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
        except Exception as error:
            self.budget.settle(
                request_id,
                cost=None,
                metadata={
                    "transport_or_accounting_failure": True,
                    "exception_type": type(error).__name__,
                    "http_status": response.status_code
                    if response is not None
                    else None,
                    "maximum_input_tokens": maximum_input,
                    "maximum_output_tokens": maximum_output,
                    "model": MODEL,
                },
            )
            raise
        self.budget.settle(request_id, cost=cost, metadata=metadata)
        return response


def native_schema(value):
    if isinstance(value, list):
        return [native_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {
        key: native_schema(item)
        for key, item in value.items()
        if key not in {"minLength", "maxLength", "minItems", "maxItems"}
    }
    if result.get("type") == "object":
        result["additionalProperties"] = False
    return result


class AnthropicGenerationProvider(OpenRouterGenerationProvider):
    """Native structured generation using the existing complete-history validator."""

    def __init__(
        self,
        key,
        *,
        transport=None,
        prompt_history_limit=None,
        workspace_id=None,
        settings=None,
        budget=None,
        production=False,
    ):
        super().__init__(
            (settings or Settings(_env_file=None)).model_copy(
                update={"openrouter_keyword_fallback_models": ""}
            ),
            transport=transport,
            prompt_history_limit=prompt_history_limit,
            candidate_batch_size=30 if production else 10,
            oversample=production,
            subfamily_prompt=production,
        )
        if production and budget is None:
            raise ValueError(
                "Production native generation requires a persistent spend budget"
            )
        self._budget = budget
        self._production = production
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
        if schema_name not in {
            "keyword_candidates",
            "source_niche_proposals",
        } or model not in (None, MODEL):
            raise KeywordSpendError("Unexpected native generation model or task")
        started = time.monotonic()
        self._last_request_metrics = {}
        schema = native_schema(response_schema)
        if schema_name == "keyword_candidates":
            requested = response_schema["properties"]["keywords"]["maxItems"]
            schema["properties"]["keywords"]["description"] = (
                f"Up to {requested} categories, each 2 to 60 characters. An empty array is valid when no unused categories remain."
            )
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
        inner = self._transport or httpx.HTTPTransport(retries=0)
        guarded = inner
        if self._budget is not None:
            guarded = AnthropicBudgetTransport(
                self._budget, inner, max_output_tokens=2000
            )
            guarded.trial = schema_name
        try:
            with httpx.Client(
                timeout=self._remaining(deadline), transport=guarded
            ) as client:
                response = client.post(
                    "https://api.anthropic.com/v1/messages",
                    json=payload,
                    headers=self._headers,
                )
        except KeywordSpendError as error:
            if not self._production:
                raise
            failure = generation_error(GenerationErrorCode.SPEND_BLOCKED)
            failure.message = str(error)
            raise failure from error
        except httpx.TimeoutException as error:
            raise generation_error(GenerationErrorCode.TIMEOUT) from error
        except httpx.HTTPError as error:
            raise generation_error(GenerationErrorCode.PROVIDER_UNREACHABLE) from error
        finally:
            if self._transport is None:
                inner.close()
        if response.status_code >= 400:
            code = {
                401: GenerationErrorCode.INVALID_API_KEY,
                402: GenerationErrorCode.INSUFFICIENT_CREDIT,
                403: GenerationErrorCode.MODEL_FORBIDDEN,
                429: GenerationErrorCode.RATE_LIMITED,
                500: GenerationErrorCode.PROVIDER_UNAVAILABLE,
                503: GenerationErrorCode.PROVIDER_UNAVAILABLE,
                529: GenerationErrorCode.PROVIDER_UNAVAILABLE,
            }.get(response.status_code, GenerationErrorCode.PROVIDER_REJECTED)
            failure = generation_error(code)
            failure.message = failure.message.replace("OpenRouter", "Anthropic")
            raise failure
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
        try:
            if data["stop_reason"] == "max_tokens":
                raise generation_error(GenerationErrorCode.TRUNCATED)
            return "".join(
                block["text"] for block in data["content"] if block["type"] == "text"
            )
        except (KeyError, TypeError, ValueError) as error:
            raise generation_error(GenerationErrorCode.MALFORMED) from error
