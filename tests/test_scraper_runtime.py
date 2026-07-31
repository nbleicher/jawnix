from __future__ import annotations

import copy
import uuid

from sqlalchemy import func, select

from jawnix.api import app
from jawnix.models import (
    AuditEntry,
    ScraperConfiguration,
    ScraperRuntimeConfigurationRevision,
    SourceSegment,
)
from jawnix.scraper_runtime import (
    RuntimeConfiguration,
    runtime_version,
)
from scraper_fake import RUNTIME_CONFIGURATION, ScraperFake
from test_scraper_workspace import (  # noqa: F401 — shared fixtures
    enter_and_verify,
    workspace_client,
    workspace_settings,
)


def arm(fake: ScraperFake) -> ScraperFake:
    app.state.scraper_operations = fake
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


def proposed_configuration() -> dict:
    configuration = copy.deepcopy(RUNTIME_CONFIGURATION)
    configuration["states"] = ["OH", "PA"]
    configuration["settings"]["depth"] = 5
    configuration["queue"]["target_depth"] = 80
    configuration["overrides"] = {
        "OH": {"cell_size_km": 30.0, "zoom": 16}
    }
    return configuration


def test_campaign_history_preserves_filters_sort_and_row_detail(
    workspace_client,
):
    client, _, fake = privileged(workspace_client)

    response = client.get(
        "/api/admin/scraper/history",
        params={
            "search": "farm",
            "state": "ky",
            "sort": "cells_posted",
            "direction": "asc",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["search"] == "farm"
    assert body["state"] == "KY"
    assert body["sort"] == "cells_posted"
    assert body["direction"] == "asc"
    assert body["rows"] == [
        {
            "keyword": "Farm Equipment Dealer",
            "state": "KY",
            "cells_posted": 324,
            "first_enqueued": "Jul 28, 01:49",
            "latest_enqueued": "Jul 29, 00:03",
            "campaign_date": "Jul 29, 2026",
        }
    ]
    assert fake.history_calls[-1] == {
        "search": "farm",
        "state": "ky",
        "sort": "cells_posted",
        "direction": "asc",
    }


def test_campaign_history_rejects_invalid_filters_before_upstream(
    workspace_client,
):
    client, _, fake = privileged(workspace_client)
    calls = len(fake.operation_calls)

    invalid_state = client.get(
        "/api/admin/scraper/history",
        params={"state": "ZZ"},
    )
    invalid_sort = client.get(
        "/api/admin/scraper/history",
        params={"sort": "last_enqueued;drop table"},
    )

    assert invalid_state.status_code == 422
    assert invalid_sort.status_code == 422
    assert len(fake.operation_calls) == calls


def test_runtime_workspace_keeps_current_controls_and_bounds(
    workspace_client,
):
    client, _, _ = privileged(workspace_client)

    response = client.get("/api/admin/scraper/runtime")

    assert response.status_code == 200
    body = response.json()
    assert body["current"] == RUNTIME_CONFIGURATION
    assert body["version"] == runtime_version(
        RuntimeConfiguration.model_validate(RUNTIME_CONFIGURATION)
    )
    assert len(body["all_states"]) == 51
    assert body["cells"] == [
        {"state": "KY", "cells": 324},
        {"state": "OH", "cells": 240},
    ]
    assert body["total_cells"] == 564
    assert body["bounds"]["runtime"]["radius"] == {
        "minimum": 100.0,
        "maximum": 100000.0,
        "step": 1.0,
    }
    assert body["bounds"]["queue"]["poll_secs"]["minimum"] == 5.0
    assert body["bounds"]["override"]["cell_size_km"]["step"] == 0.5


def test_preview_explains_calculated_effects_and_changes_nothing(
    workspace_client,
):
    client, csrf, fake = privileged(workspace_client)
    proposal = proposed_configuration()

    response = post(
        client,
        csrf,
        "/api/admin/scraper/runtime/preview",
        {"configuration": proposal},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["review_token"]
    assert body["expected_version"] == runtime_version(
        RuntimeConfiguration.model_validate(RUNTIME_CONFIGURATION)
    )
    assert body["proposed_version"] == runtime_version(
        RuntimeConfiguration.model_validate(proposal)
    )
    assert body["effects"] == {
        "cells": [
            {"state": "OH", "cells": 120},
            {"state": "PA", "cells": 220},
        ],
        "current_total_cells": 564,
        "proposed_total_cells": 340,
        "total_cell_delta": -224,
        "states_added": ["PA"],
        "states_removed": ["KY"],
        "runtime_changes": ["depth"],
        "queue_changes": ["target_depth"],
        "override_changes": ["OH"],
    }
    assert fake.runtime == RUNTIME_CONFIGURATION
    assert fake.runtime_writes == []


def test_runtime_validation_keeps_current_bounds_and_cross_field_rules(
    workspace_client,
):
    client, csrf, fake = privileged(workspace_client)
    invalid = proposed_configuration()
    invalid["settings"]["zoom"] = 22
    invalid["queue"]["min_target_depth"] = 600
    invalid["queue"]["max_target_depth"] = 500

    response = post(
        client,
        csrf,
        "/api/admin/scraper/runtime/preview",
        {"configuration": invalid},
    )

    assert response.status_code == 422
    assert fake.runtime_writes == []
    assert "runtime_preview" not in fake.operation_calls


def test_save_requires_a_review_of_the_exact_configuration(
    workspace_client,
):
    client, csrf, fake = privileged(workspace_client)

    response = post(
        client,
        csrf,
        "/api/admin/scraper/runtime/save",
        {
            "configuration": proposed_configuration(),
            "expected_version": "0" * 64,
            "review_token": "not-a-review",
            "reason": "Tune the next campaign",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == (
        "Preview these runtime changes again before saving."
    )
    assert fake.runtime_writes == []


def test_reviewed_save_preserves_enqueue_and_journals_safe_activity(
    workspace_client,
    session,
):
    client, csrf, fake = privileged(workspace_client)
    proposal = proposed_configuration()
    preview = post(
        client,
        csrf,
        "/api/admin/scraper/runtime/preview",
        {"configuration": proposal},
    ).json()

    response = post(
        client,
        csrf,
        "/api/admin/scraper/runtime/save",
        {
            "configuration": proposal,
            "expected_version": preview["expected_version"],
            "review_token": preview["review_token"],
            "enqueue": True,
            "reason": "Raise depth for the reviewed states",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["enqueued"] is True
    assert body["configuration"] == proposal
    assert fake.runtime == proposal
    assert fake.runtime_writes[0]["configuration"]["states"] == ["OH", "PA"]
    assert fake.runtime_writes[0]["enqueue"] is True
    revision = session.scalars(
        select(ScraperRuntimeConfigurationRevision)
    ).one()
    assert str(revision.id) == body["revision_id"]
    assert revision.before_checksum == preview["expected_version"]
    assert revision.after_checksum == preview["proposed_version"]
    assert "api_base" not in revision.configuration
    assert revision.configuration["activeStates"] == ["OH", "PA"]
    assert revision.enqueue_requested is True
    entry = session.scalars(
        select(AuditEntry).where(
            AuditEntry.action == "scraper_runtime_configuration_saved"
        )
    ).one()
    assert entry.target_id == str(revision.id)
    assert entry.reason == "Raise depth for the reviewed states"
    assert entry.details["before"]["activeStates"] == ["KY", "OH"]
    assert entry.details["after"]["activeStates"] == ["OH", "PA"]
    assert entry.details["enqueueRequested"] is True
    assert entry.details["jawnixConfigurationChanged"] is False


def test_runtime_save_does_not_rewrite_jawnix_scraper_configuration(
    workspace_client,
    session,
):
    immutable = ScraperConfiguration(
        version=7,
        checksum="a" * 64,
        status="active",
        anomaly_thresholds={
            "down_fraction": 0.5,
            "up_multiplier": 2.0,
            "history_runs": 7,
        },
        created_by=uuid.uuid4(),
        reason="Published acquisition version",
        segments=[
            SourceSegment(
                key="plumbers-oh",
                niche="Plumbers",
                query="plumbers",
                geography="OH",
                parameters={"zoom": 13},
            )
        ],
    )
    session.add(immutable)
    session.commit()
    configuration_id = immutable.id
    segment_id = immutable.segments[0].id
    client, csrf, _ = privileged(workspace_client)
    proposal = proposed_configuration()
    preview = post(
        client,
        csrf,
        "/api/admin/scraper/runtime/preview",
        {"configuration": proposal},
    ).json()

    saved = post(
        client,
        csrf,
        "/api/admin/scraper/runtime/save",
        {
            "configuration": proposal,
            "expected_version": preview["expected_version"],
            "review_token": preview["review_token"],
            "reason": "Scale worker tuning only",
        },
    )

    assert saved.status_code == 200
    session.expire_all()
    preserved = session.get(ScraperConfiguration, configuration_id)
    assert preserved.version == 7
    assert preserved.checksum == "a" * 64
    assert preserved.status == "active"
    assert preserved.anomaly_thresholds["history_runs"] == 7
    assert preserved.segments[0].id == segment_id
    assert preserved.segments[0].parameters == {"zoom": 13}
    assert session.scalar(select(func.count(ScraperConfiguration.id))) == 1
    assert session.scalar(select(func.count(SourceSegment.id))) == 1


def test_concurrent_runtime_change_is_refused_and_audited(
    workspace_client,
    session,
):
    client, csrf, fake = privileged(workspace_client)
    proposal = proposed_configuration()
    preview = post(
        client,
        csrf,
        "/api/admin/scraper/runtime/preview",
        {"configuration": proposal},
    ).json()
    fake.runtime["queue"]["batch_size"] = 200

    response = post(
        client,
        csrf,
        "/api/admin/scraper/runtime/save",
        {
            "configuration": proposal,
            "expected_version": preview["expected_version"],
            "review_token": preview["review_token"],
            "reason": "Tune queue",
        },
    )

    assert response.status_code == 409
    assert "preview again" in response.json()["detail"]
    assert fake.runtime_writes == []
    assert session.scalars(
        select(AuditEntry).where(
            AuditEntry.action
            == "scraper_runtime_configuration_save_refused"
        )
    ).one()


def test_history_and_runtime_failures_are_recoverable_and_safe(
    workspace_client,
    session,
):
    client, csrf, _ = privileged(
        workspace_client,
        ScraperFake(runtime_failing={"history", "configure"}),
    )

    history = client.get("/api/admin/scraper/history")
    runtime = client.get("/api/admin/scraper/runtime")
    assert history.status_code == 200
    assert runtime.status_code == 200
    assert history.json()["service_state"] == "unavailable"
    assert history.json()["rows"] == []
    assert runtime.json()["service_state"] == "unavailable"
    assert runtime.json()["cells"] == []
    assert "10.77.0.2" not in history.text + runtime.text

    fake = arm(ScraperFake())
    preview = post(
        client,
        csrf,
        "/api/admin/scraper/runtime/preview",
        {"configuration": proposed_configuration()},
    ).json()
    fake.runtime_failing.add("save")
    failed = post(
        client,
        csrf,
        "/api/admin/scraper/runtime/save",
        {
            "configuration": proposed_configuration(),
            "expected_version": preview["expected_version"],
            "review_token": preview["review_token"],
            "reason": "Tune runtime",
        },
    )
    assert failed.status_code == 503
    assert "save unavailable" not in failed.text
    assert session.scalars(
        select(AuditEntry).where(
            AuditEntry.action
            == "scraper_runtime_configuration_save_failed"
        )
    ).one()


def test_every_history_and_runtime_route_requires_privileged_session(
    workspace_client,
):
    client, csrf, _, _ = workspace_client
    fake = arm(ScraperFake())
    proposal = proposed_configuration()
    requests = [
        client.get("/api/admin/scraper/history"),
        client.get("/api/admin/scraper/runtime"),
        post(
            client,
            csrf,
            "/api/admin/scraper/runtime/preview",
            {"configuration": proposal},
        ),
        post(
            client,
            csrf,
            "/api/admin/scraper/runtime/save",
            {
                "configuration": proposal,
                "expected_version": "0" * 64,
                "review_token": "not-a-review",
                "reason": "Tune runtime",
            },
        ),
    ]

    assert [response.status_code for response in requests] == [401] * 4
    assert fake.operation_calls == []
