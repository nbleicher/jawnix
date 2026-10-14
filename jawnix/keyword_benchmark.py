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

from .anthropic_generation import (
    MODEL,
    money,
)
from .anthropic_generation import (
    AnthropicBudgetTransport as BudgetedTransport,
)
from .anthropic_generation import (
    AnthropicGenerationProvider as AnthropicHaikuProvider,
)
from .anthropic_generation import (
    KeywordSpendError as BenchmarkBudgetError,
)
from .anthropic_generation import anthropic_cost as anthropic_cost  # noqa: PLC0414 - public re-export
from .keyword_generation import (
    KEYWORD_INDUSTRY_FOCI,
    KeywordGenerationError,
)


def save_json(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.chmod(0o600)
    os.replace(temporary, path)


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

    def account_uncertain_at_upper_bound(self, request_id: str, *, reason: str):
        """Resume without refunding any part of a bounded, uncertain request."""
        with self.locked() as state:
            call = next(c for c in state["calls"] if c["id"] == request_id)
            if call["status"] != "uncertain" or call["metered_usd"] is not None:
                raise BenchmarkBudgetError("Only an uncertain bounded request may be accounted conservatively")
            if state["blocked_reason"] != "Request cost is unknown; further spending is blocked":
                raise BenchmarkBudgetError("The request's upper bound is not valid for resumption")
            call.update(status="conservative", accounting_reason=reason,
                accounted_at=datetime.now(timezone.utc).isoformat())
            if not any(c["status"] == "uncertain" for c in state["calls"]):
                state["blocked_reason"] = None

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
