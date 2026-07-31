from __future__ import annotations

from sqlalchemy import select

from jawnix.api import app
from jawnix.models import AuditEntry
from jawnix.scraper_database import DatabaseExport
from scraper_fake import ScraperFake
from test_scraper_workspace import (  # noqa: F401 — shared fixtures
    enter_and_verify,
    workspace_client,
    workspace_settings,
)


def privileged(workspace_client, fake: ScraperFake | None = None):
    client, csrf, _, _ = workspace_client
    fake = fake or ScraperFake()
    app.state.scraper_operations = fake
    enter_and_verify(client, csrf)
    return client, csrf, fake


def test_database_browse_preserves_totals_filters_paging_and_fields(
    workspace_client,
):
    client, _, fake = privileged(workspace_client)

    response = client.get(
        "/api/admin/scraper/database",
        params={"search": "plumbing", "state": "oh", "page": 1},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["service_state"] == "connected"
    assert body["totals"] == {
        "businesses": 9_244_326,
        "unique_phones": 2_305_025,
    }
    assert body["states"][0] == {
        "state": "OH",
        "businesses": 136_150,
        "unique_phones": 71_204,
        "niches": 25,
    }
    assert body["browse"] == {
        "records": [
            {
                "title": "Buckeye Plumbing",
                "phone": "(614) 555-0101",
                "website": "https://buckeye.example",
                "state": "OH",
                "niche": "plumbers",
                "last_seen": "Jul 28, 11:59",
            },
            {
                "title": "Capital Electric",
                "phone": "614-555-0101",
                "website": None,
                "state": "OH",
                "niche": "electricians",
                "last_seen": "Jul 28, 11:58",
            },
        ],
        "search": "plumbing",
        "state": "OH",
        "page": 1,
        "page_size": 50,
        "total": 51,
        "pages": 2,
        "has_previous": False,
        "has_next": True,
    }
    assert [
        item["filename"] for item in body["stored_exports"]
    ] == ["OH.csv", "PA.csv"]
    assert fake.database_calls[0] == ("workspace", {
        "search": "plumbing",
        "state": "oh",
        "page": 1,
    })


def test_state_detail_preserves_niche_context_and_totals(workspace_client):
    client, _, fake = privileged(workspace_client)

    response = client.get("/api/admin/scraper/database/states/oh")

    assert response.status_code == 200
    assert response.json()["totals"] == {
        "state": "OH",
        "businesses": 136_150,
        "unique_phones": 71_204,
        "niches": 2,
    }
    assert response.json()["niches"] == [
        {
            "key": "plumbers",
            "label": "plumbers",
            "businesses": 80_000,
            "unique_phones": 42_000,
        },
        {
            "key": "__uncategorized__",
            "label": "Uncategorized",
            "businesses": 56_150,
            "unique_phones": 29_204,
        },
    ]
    assert fake.database_calls[-1] == ("state", "oh")


def test_single_niche_and_multi_state_csv_keep_data_and_filename_semantics(
    workspace_client,
):
    client, _, fake = privileged(workspace_client)

    state_export = client.get(
        "/api/admin/scraper/database/exports/state/OH",
        params={"scope": "selected", "niche": "plumbers"},
    )
    bulk_export = client.get(
        "/api/admin/scraper/database/exports/states",
        params=[("state", "OH"), ("state", "PA")],
    )

    assert state_export.status_code == 200
    assert state_export.headers["content-type"].startswith("text/csv")
    assert state_export.headers["content-disposition"] == (
        'attachment; filename="OH-plumbers-phone-leads-2026-07-29.csv"'
    )
    assert state_export.text.splitlines() == [
        "business_name,phone_number,state",
        "Buckeye Plumbing,6145550101,OH",
    ]
    assert bulk_export.status_code == 200
    assert bulk_export.headers["content-disposition"] == (
        'attachment; filename="OH-PA-phone-leads-2026-07-29.csv"'
    )
    assert bulk_export.text.splitlines() == [
        "business_name,phone_number,state",
        "OH Business,5550000000,OH",
        "PA Business,5550000000,PA",
    ]
    _, (_, state_payload) = fake.database_calls[-2]
    _, bulk_payload = fake.database_calls[-1]
    assert state_payload.niches == ["plumbers"]
    assert bulk_payload.states == ["oh", "pa"]


def test_regeneration_is_audited_and_stored_exports_remain_downloadable(
    workspace_client,
    session,
):
    client, csrf, _ = privileged(workspace_client)

    regenerated = client.post(
        "/api/admin/scraper/database/exports/OH/regenerate",
        headers={"X-CSRF-Token": csrf},
    )
    stored = client.get(
        "/api/admin/scraper/database/exports/stored/OH.csv"
    )

    assert regenerated.status_code == 200
    assert regenerated.json() == {
        "generated": "OH.csv",
        "stored_exports": [
            {"filename": "OH.csv", "size_label": "42.5 KB"},
            {"filename": "PA.csv", "size_label": "38.0 KB"},
        ],
    }
    assert stored.status_code == 200
    assert stored.headers["content-disposition"] == (
        'attachment; filename="OH.csv"'
    )
    assert stored.text.startswith("phone,title\n")
    entry = session.scalars(
        select(AuditEntry).where(
            AuditEntry.action == "scraper_exports_regenerated"
        )
    ).one()
    assert entry.target_type == "scraper_export"
    assert entry.target_id == "OH"
    assert entry.details == {
        "requestedState": "OH",
        "generated": "OH.csv",
        "storedExportCount": 2,
    }


def test_invalid_export_inputs_are_rejected_before_upstream(workspace_client):
    client, _, fake = privileged(workspace_client)
    calls_before = len(fake.database_calls)

    missing_state = client.get(
        "/api/admin/scraper/database/exports/states"
    )
    missing_niche = client.get(
        "/api/admin/scraper/database/exports/state/OH",
        params={"scope": "selected"},
    )
    invalid_filename = client.get(
        "/api/admin/scraper/database/exports/stored/not-a-state.txt"
    )

    assert missing_state.status_code == 422
    assert missing_niche.status_code == 422
    assert invalid_filename.status_code == 400
    assert len(fake.database_calls) == calls_before


def test_authorization_and_privileged_session_guard_every_database_action(
    workspace_client,
):
    client, _, _, _ = workspace_client

    assert client.get("/api/admin/scraper/database").status_code == 401
    assert (
        client.get(
            "/api/admin/scraper/database/exports/stored/OH.csv"
        ).status_code
        == 401
    )


def test_service_failures_and_bad_headers_never_leak_private_details(
    workspace_client,
    workspace_settings,
):
    client, _, _ = privileged(
        workspace_client,
        ScraperFake(offline=True),
    )

    browse = client.get("/api/admin/scraper/database")
    download = client.get(
        "/api/admin/scraper/database/exports/state/OH"
    )

    assert browse.status_code == 200
    assert browse.json()["service_state"] == "unavailable"
    assert download.status_code == 503
    for value in (
        workspace_settings.scraper_ops_url,
        workspace_settings.scraper_ops_user,
        workspace_settings.scraper_ops_password,
    ):
        assert value not in browse.text
        assert value not in download.text

    class PoisonedExportFake(ScraperFake):
        async def export_database_state(self, state, payload):
            return DatabaseExport(
                filename="OH.csv\r\nX-Internal.csv",
                content="private-host=10.77.0.2",
            )

    app.state.scraper_operations = PoisonedExportFake()
    poisoned = client.get(
        "/api/admin/scraper/database/exports/state/OH"
    )

    assert poisoned.status_code == 503
    assert "10.77.0.2" not in poisoned.text
    assert "X-Internal" not in poisoned.text


def test_declared_download_errors_keep_the_existing_public_messages(
    workspace_client,
):
    class MissingExportFake(ScraperFake):
        async def stored_database_export(self, filename):
            from jawnix.scraper_operations import ScraperOperationsError

            raise ScraperOperationsError(
                status_code=404,
                detail="private storage location is gone",
            )

    client, _, _ = privileged(
        workspace_client,
        MissingExportFake(),
    )

    response = client.get(
        "/api/admin/scraper/database/exports/stored/OH.csv"
    )

    assert response.status_code == 404
    assert response.json() == {
        "detail": "The requested Scraper export was not found."
    }
    assert "private storage location" not in response.text
