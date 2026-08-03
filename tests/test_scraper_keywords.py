from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from jawnix.api import app
from jawnix.keyword_generation import (
    GenerationErrorCode,
    KeywordGenerationResult,
    generation_error,
)
from jawnix.models import (
    AuditEntry,
    KeywordGenerationDraftRecord,
    KeywordHistory,
)
from jawnix.scraper_keywords import keyword_version
from scraper_fake import GenerationFake, KEYWORDS, ScraperFake
from test_scraper_workspace import (  # noqa: F401 — shared fixtures
    enter_and_verify,
    workspace_client,
    workspace_settings,
)


def arm(fake: ScraperFake) -> ScraperFake:
    app.state.scraper_operations = fake
    app.state.keyword_generation_provider = GenerationFake(
        available=fake.ai_enabled
    )
    return fake


def arm_generation(fake: GenerationFake) -> GenerationFake:
    app.state.keyword_generation_provider = fake
    return fake


def privileged(workspace_client, fake: ScraperFake | None = None):
    client, csrf, _, _ = workspace_client
    fake = arm(fake or ScraperFake())
    enter_and_verify(client, csrf)
    return client, csrf, fake


def post(client, csrf, path, body):
    return client.post(
        path,
        headers={"X-CSRF-Token": csrf},
        json=body,
    )


def test_keyword_workspace_projects_editor_rankings_and_rollover(
    workspace_client,
    session,
):
    client, _, fake = privileged(workspace_client)

    response = client.get("/api/admin/scraper/keywords")

    assert response.status_code == 200
    body = response.json()
    assert body["current"] == KEYWORDS
    assert body["version"] == keyword_version(KEYWORDS)
    assert body["ai_enabled"] is True
    assert body["rollover"] == {
        "enabled": False,
        "state": "off",
        "label": "Off",
        "detail": "Manual keyword batches",
        "percent_complete": 60,
        "posted_jobs": None,
        "expected_jobs": None,
        "last_status": "generated",
        "last_event": "Jul 28 · 12:00 UTC",
    }
    assert body["winners"][0] == {
        "rank": 1,
        "keyword": "plumbers",
        "phone_businesses": 2480,
        "businesses": 4000,
        "posted_cells": 1000,
        "phones_per_cell": 2.48,
        "phone_rate": 0.62,
        "last_used": "Jul 28",
    }
    assert sorted(fake.keyword_calls) == ["list", "winners"]
    assert set(
        session.execute(
            select(KeywordHistory.term, KeywordHistory.origin)
        ).all()
    ) == {
        ("electricians", "active_list"),
        ("plumbers", "active_list"),
        ("plumbers", "winner"),
        ("roof repair", "winner"),
    }
    now = workspace_client[3]
    now[0] += timedelta(minutes=10)
    assert client.get("/api/admin/scraper/keywords").status_code == 200
    active = session.scalars(
        select(KeywordHistory).where(
            KeywordHistory.term == "electricians",
            KeywordHistory.origin == "active_list",
        )
    ).one()
    assert active.first_seen_at.replace(tzinfo=timezone.utc) == datetime(
        2026,
        7,
        28,
        12,
        tzinfo=timezone.utc,
    )
    assert active.last_seen_at.replace(tzinfo=timezone.utc) == datetime(
        2026,
        7,
        28,
        12,
        10,
        tzinfo=timezone.utc,
    )


def test_preview_uses_the_supported_text_format_and_changes_nothing(
    workspace_client,
):
    client, csrf, fake = privileged(workspace_client)

    response = post(
        client,
        csrf,
        "/api/admin/scraper/keywords/preview",
        {"text": " Plumbers \nroofers\nROOFERS\n# ignored\n"},
    )

    assert response.status_code == 200
    body = response.json()
    review_token = body.pop("review_token")
    assert review_token
    assert body == {
        "proposed": ["Plumbers", "roofers"],
        "added": ["roofers"],
        "removed": ["electricians"],
        "unchanged": ["Plumbers"],
        "expected_version": keyword_version(KEYWORDS),
    }
    assert fake.keywords == KEYWORDS
    assert fake.keyword_writes == []


def test_empty_keyword_input_is_refused_before_save(workspace_client):
    client, csrf, fake = privileged(workspace_client)

    preview = post(
        client,
        csrf,
        "/api/admin/scraper/keywords/preview",
        {"text": "\n# only a comment\n"},
    )
    save = post(
        client,
        csrf,
        "/api/admin/scraper/keywords/save",
        {
            "text": "\n# only a comment\n",
            "expected_version": keyword_version(KEYWORDS),
        },
    )

    assert preview.status_code == 422
    assert save.status_code == 422
    assert fake.keyword_writes == []


def test_save_requires_a_review_of_the_exact_proposed_list(workspace_client):
    client, csrf, fake = privileged(workspace_client)

    response = post(
        client,
        csrf,
        "/api/admin/scraper/keywords/save",
        {
            "text": "plumbers\nroofers",
            "expected_version": keyword_version(KEYWORDS),
            "review_token": "not-a-review",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == (
        "Preview these keyword changes again before saving."
    )
    assert fake.keyword_writes == []


def test_reviewed_save_preserves_enqueue_and_is_audited(
    workspace_client,
    session,
):
    client, csrf, fake = privileged(workspace_client)
    text = "Plumbers\nroofers\n"
    preview = post(
        client,
        csrf,
        "/api/admin/scraper/keywords/preview",
        {"text": text},
    ).json()

    response = post(
        client,
        csrf,
        "/api/admin/scraper/keywords/save",
        {
            "text": text,
            "expected_version": preview["expected_version"],
            "review_token": preview["review_token"],
            "enqueue": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["current"] == ["Plumbers", "roofers"]
    assert response.json()["enqueued"] is True
    assert fake.keyword_writes == [
        {
            "text": "Plumbers\nroofers",
            "expected_version": preview["expected_version"],
            "enqueue": True,
        }
    ]
    entry = session.scalars(
        select(AuditEntry).where(
            AuditEntry.action == "scraper_keywords_saved"
        )
    ).one()
    assert entry.details["addedCount"] == 1
    assert entry.details["removedCount"] == 1
    assert entry.details["enqueueRequested"] is True
    assert set(
        session.scalars(
            select(KeywordHistory.term).where(
                KeywordHistory.origin == "accepted_save"
            )
        )
    ) == {"plumbers", "roofers"}


def test_concurrent_change_is_refused_and_can_be_previewed_again(
    workspace_client,
    session,
):
    client, csrf, fake = privileged(workspace_client)
    text = "plumbers\nroofers"
    first = post(
        client,
        csrf,
        "/api/admin/scraper/keywords/preview",
        {"text": text},
    ).json()
    fake.keywords.append("hvac")

    refused = post(
        client,
        csrf,
        "/api/admin/scraper/keywords/save",
        {
            "text": text,
            "expected_version": first["expected_version"],
            "review_token": first["review_token"],
        },
    )

    assert refused.status_code == 409
    assert "preview again" in refused.json()["detail"]
    assert fake.keyword_writes == []
    assert session.scalars(
        select(AuditEntry).where(
            AuditEntry.action == "scraper_keywords_save_refused"
        )
    ).one()

    refreshed = post(
        client,
        csrf,
        "/api/admin/scraper/keywords/preview",
        {"text": text},
    )
    assert refreshed.status_code == 200
    assert refreshed.json()["removed"] == ["electricians", "hvac"]
    assert refreshed.json()["expected_version"] == keyword_version(fake.keywords)


@pytest.mark.parametrize(
    ("mode", "seed"),
    [("broad", None), ("adjacent", "plumbers")],
)
def test_ai_generation_creates_only_a_review_draft(
    workspace_client,
    session,
    mode,
    seed,
):
    client, csrf, fake = privileged(workspace_client)

    response = post(
        client,
        csrf,
        "/api/admin/scraper/keywords/generate",
        {"mode": mode, "seed_keyword": seed},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == mode
    assert body["seed_keyword"] == seed
    assert len(body["keywords"]) == 25
    assert body["excluded_count"] == 7
    assert "nothing has been saved or enqueued" in body["notice"]
    assert fake.keywords == KEYWORDS
    assert fake.keyword_writes == []
    entry = session.scalars(
        select(AuditEntry).where(
            AuditEntry.action == "scraper_keyword_generation_created"
        )
    ).one()
    assert entry.details["activeConfigurationChanged"] is False
    stored = session.get(
        KeywordGenerationDraftRecord,
        uuid.UUID(body["generation_id"]),
    )
    assert stored.administrator_id is not None
    assert stored.model == "test/generation-model"
    assert stored.terms == body["keywords"]
    assert stored.acceptance_status == "pending"
    assert stored.expires_at - stored.created_at == timedelta(hours=24)
    assert stored.exclusion_metrics == {
        "activeCount": 2,
        "winnerCount": 2,
        "historyCount": 0,
        "uniqueCount": 3,
    }
    assert stored.candidate_metrics["attemptCount"] == 2


def test_generated_draft_is_accepted_only_by_a_later_reviewed_save(
    workspace_client,
    session,
):
    client, csrf, fake = privileged(workspace_client)
    generated = post(
        client,
        csrf,
        "/api/admin/scraper/keywords/generate",
        {"mode": "broad"},
    ).json()
    text = "\n".join(generated["keywords"][:-1])
    preview = post(
        client,
        csrf,
        "/api/admin/scraper/keywords/preview",
        {"text": text},
    ).json()

    saved = post(
        client,
        csrf,
        "/api/admin/scraper/keywords/save",
        {
            "text": text,
            "expected_version": preview["expected_version"],
            "review_token": preview["review_token"],
            "generation_id": generated["generation_id"],
        },
    )

    assert saved.status_code == 200
    assert len(fake.keywords) == 24
    assert "generation_id" not in fake.keyword_writes[0]
    stored = session.get(
        KeywordGenerationDraftRecord,
        uuid.UUID(generated["generation_id"]),
    )
    assert stored.acceptance_status == "accepted"
    assert stored.accepted_at is not None
    entry = session.scalars(
        select(AuditEntry).where(
            AuditEntry.action == "scraper_keywords_saved"
        )
    ).one()
    assert entry.details["generationAccepted"] is True


def test_generation_draft_stays_pending_when_the_final_save_fails(
    workspace_client,
    session,
):
    client, csrf, _ = privileged(workspace_client)
    generated = post(
        client,
        csrf,
        "/api/admin/scraper/keywords/generate",
        {"mode": "broad"},
    ).json()
    text = "\n".join(generated["keywords"])
    preview = post(
        client,
        csrf,
        "/api/admin/scraper/keywords/preview",
        {"text": text},
    ).json()
    arm(ScraperFake(offline=True))

    failed = post(
        client,
        csrf,
        "/api/admin/scraper/keywords/save",
        {
            "text": text,
            "expected_version": preview["expected_version"],
            "review_token": preview["review_token"],
            "generation_id": generated["generation_id"],
        },
    )

    assert failed.status_code == 503
    stored = session.get(
        KeywordGenerationDraftRecord,
        uuid.UUID(generated["generation_id"]),
    )
    assert stored.acceptance_status == "pending"
    assert stored.accepted_at is None


def test_generation_lock_conflict_keeps_the_existing_retryable_409(
    workspace_client,
    monkeypatch,
):
    client, csrf, _ = privileged(workspace_client)
    generator = arm_generation(GenerationFake())
    monkeypatch.setattr(
        "jawnix.scraper_proxy.try_generation_lock",
        lambda _session: False,
    )

    response = post(
        client,
        csrf,
        "/api/admin/scraper/keywords/generate",
        {"mode": "broad"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Another keyword generation is already running"
    )
    assert generator.calls == []


def test_generation_endpoint_never_returns_a_partial_provider_result(
    workspace_client,
):
    client, csrf, _ = privileged(workspace_client)

    class PartialGenerationFake(GenerationFake):
        def generate_keywords(self, **_kwargs):
            return KeywordGenerationResult(
                terms=[f"Partial Trade {index}" for index in range(24)],
                excluded_count=0,
                candidate_metrics={"attemptCount": 3},
            )

    arm_generation(PartialGenerationFake())

    response = post(
        client,
        csrf,
        "/api/admin/scraper/keywords/generate",
        {"mode": "broad"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "AI could not produce 25 sufficiently distinct keywords; try again"
    )


def test_ai_unavailable_and_provider_failure_are_recoverable_and_audited(
    workspace_client,
    session,
):
    client, csrf, _ = privileged(
        workspace_client,
        ScraperFake(ai_enabled=False),
    )

    unavailable = post(
        client,
        csrf,
        "/api/admin/scraper/keywords/generate",
        {"mode": "broad"},
    )
    assert unavailable.status_code == 422
    assert unavailable.json()["detail"] == "AI generation is not configured"

    failing = arm_generation(
        GenerationFake(
            error=generation_error(GenerationErrorCode.PROVIDER_UNAVAILABLE)
        )
    )
    failed = post(
        client,
        csrf,
        "/api/admin/scraper/keywords/generate",
        {"mode": "broad"},
    )
    assert failed.status_code == 503
    assert "temporarily unavailable" in failed.json()["detail"]
    assert len(failing.calls) == 1
    assert len(
        session.scalars(
            select(AuditEntry).where(
                AuditEntry.action == "scraper_keyword_generation_failed"
            )
        ).all()
    ) == 2


def test_generation_timeout_is_diagnosable_and_audited(
    workspace_client,
    session,
):
    client, csrf, _ = privileged(workspace_client)
    arm_generation(
        GenerationFake(error=generation_error(GenerationErrorCode.TIMEOUT))
    )

    response = post(
        client,
        csrf,
        "/api/admin/scraper/keywords/generate",
        {"mode": "broad"},
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "The AI provider timed out; try again"
    }
    entry = session.scalars(
        select(AuditEntry).where(
            AuditEntry.action == "scraper_keyword_generation_failed"
        )
    ).one()
    assert entry.details["outcome"] == "provider_timeout"


def test_preview_transport_failure_is_audited(workspace_client, session):
    client, csrf, _ = privileged(
        workspace_client,
        ScraperFake(offline=True),
    )

    response = post(
        client,
        csrf,
        "/api/admin/scraper/keywords/preview",
        {"text": "plumbers"},
    )

    assert response.status_code == 503
    entry = session.scalars(
        select(AuditEntry).where(
            AuditEntry.action == "scraper_keywords_preview_failed"
        )
    ).one()
    assert entry.details == {"outcome": "upstream_unavailable"}


def test_winner_must_still_be_ranked_when_adjacent_generation_starts(
    workspace_client,
):
    client, csrf, fake = privileged(workspace_client)

    response = post(
        client,
        csrf,
        "/api/admin/scraper/keywords/generate",
        {"mode": "adjacent", "seed_keyword": "not a winner"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "The selected winner is unavailable"
    assert fake.keyword_writes == []


def test_rollover_controls_keep_state_metrics_and_audit(
    workspace_client,
    session,
):
    client, csrf, fake = privileged(workspace_client)

    enabled = post(
        client,
        csrf,
        "/api/admin/scraper/keywords/rollover",
        {"action": "enable"},
    )
    disabled = post(
        client,
        csrf,
        "/api/admin/scraper/keywords/rollover",
        {"action": "disable"},
    )

    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True
    assert enabled.json()["state"] == "working"
    assert enabled.json()["posted_jobs"] == 12
    assert enabled.json()["expected_jobs"] == 20
    assert disabled.status_code == 200
    assert disabled.json()["state"] == "off"
    assert fake.rollover_enabled is False
    actions = set(
        session.scalars(
            select(AuditEntry.action).where(
                AuditEntry.target_type == "scraper_keyword_rollover"
            )
        )
    )
    assert actions == {
        "scraper_keyword_rollover_enabled",
        "scraper_keyword_rollover_disabled",
    }


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", "/api/admin/scraper/keywords", None),
        ("post", "/api/admin/scraper/keywords/preview", {"text": "plumbers"}),
        (
            "post",
            "/api/admin/scraper/keywords/save",
            {
                "text": "plumbers",
                "expected_version": "0" * 64,
                "review_token": "not-a-review",
            },
        ),
        (
            "post",
            "/api/admin/scraper/keywords/generate",
            {"mode": "broad"},
        ),
        (
            "post",
            "/api/admin/scraper/keywords/rollover",
            {"action": "enable"},
        ),
    ],
)
def test_every_keyword_action_requires_the_privileged_session(
    workspace_client,
    method,
    path,
    body,
):
    client, csrf, _, _ = workspace_client
    fake = arm(ScraperFake())

    response = (
        client.get(path, headers={"X-CSRF-Token": csrf})
        if method == "get"
        else client.post(
            path,
            headers={"X-CSRF-Token": csrf},
            json=body,
        )
    )

    assert response.status_code == 401
    assert fake.keyword_calls == []


def test_upstream_failure_is_recoverable_without_leaking_details(
    workspace_client,
    session,
):
    client, csrf, _ = privileged(workspace_client)
    preview = post(
        client,
        csrf,
        "/api/admin/scraper/keywords/preview",
        {"text": "plumbers"},
    ).json()
    arm(ScraperFake(offline=True))

    response = post(
        client,
        csrf,
        "/api/admin/scraper/keywords/save",
        {
            "text": "plumbers",
            "expected_version": preview["expected_version"],
            "review_token": preview["review_token"],
        },
    )

    assert response.status_code == 503
    assert "scraper unreachable" not in response.text
    assert session.scalars(
        select(AuditEntry).where(
            AuditEntry.action == "scraper_keywords_save_failed"
        )
    ).one()


def test_keyword_workspace_degrades_in_place_when_upstream_is_unavailable(
    workspace_client,
):
    client, _, _ = privileged(workspace_client, ScraperFake(offline=True))

    response = client.get("/api/admin/scraper/keywords")

    assert response.status_code == 200
    assert response.json() == {
        "service_state": "unavailable",
        "last_successful_at": None,
        "current": [],
        "version": keyword_version([]),
        "ai_enabled": False,
        "rollover": {
            "enabled": False,
            "state": "off",
            "label": "Unavailable",
            "detail": "No current rollover status is available.",
            "percent_complete": 0,
            "posted_jobs": None,
            "expected_jobs": None,
            "last_status": None,
            "last_event": None,
        },
        "winners": [],
        "idle_expires_in": 900,
    }
