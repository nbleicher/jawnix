from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from jawnix.api import app
from jawnix.models import AuditEntry
from jawnix.scraper_keywords import keyword_version
from scraper_fake import KEYWORDS, ScraperFake
from test_scraper_workspace import (  # noqa: F401 — shared fixtures
    enter_and_verify,
    workspace_client,
    workspace_settings,
)


def arm(fake: ScraperFake) -> ScraperFake:
    app.state.scraper_proxy_transport = httpx.MockTransport(fake)
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
    assert [call.url.path for call in fake.calls[-2:]] == [
        "/keywords",
        "/keywords/winners",
    ]


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
            "enqueue": "true",
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
    assert fake.keyword_writes[0]["generation_id"] == generated["generation_id"]
    entry = session.scalars(
        select(AuditEntry).where(
            AuditEntry.action == "scraper_keywords_saved"
        )
    ).one()
    assert entry.details["generationAccepted"] is True


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

    failing = arm(
        ScraperFake(
            generation_error="DeepSeek is temporarily unavailable; try again"
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
    assert failing.keywords == KEYWORDS
    assert len(
        session.scalars(
            select(AuditEntry).where(
                AuditEntry.action == "scraper_keyword_generation_failed"
            )
        ).all()
    ) == 2


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
    assert fake.calls == []


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
