from __future__ import annotations

import csv
import json
import uuid

from jawnix.models import Agency, Agent, CustomerProfile
from jawnix_data.configuration import prepare_agent_config
from jawnix_data.customer_mappings import provision_customer_mappings


def test_prepare_config_applies_explicit_state_and_agent_overrides(tmp_path):
    source = tmp_path / "source.json"
    overrides = tmp_path / "overrides.json"
    destination = tmp_path / "staging" / "config.json"
    source.write_text(
        json.dumps(
            {
                "agents": ["jo"],
                "agent_states": {"jo": ["IO", "TX"], "tony-aca": ["CN", "IO"]},
                "agencies": {"summit": ["jo"]},
            }
        ),
        encoding="utf-8",
    )
    overrides.write_text(
        json.dumps(
            {
                "state_corrections": {"IO": "IA", "CN": "CT"},
                "agent_additions": [
                    {"slug": "matthew", "agency": "summit"},
                    {"slug": "ali", "agency": "summit"},
                ],
            }
        ),
        encoding="utf-8",
    )

    result = prepare_agent_config(source, destination, overrides)
    prepared = json.loads(destination.read_text(encoding="utf-8"))
    assert result["stateCorrections"] == {"IO": 2, "CN": 1}
    assert prepared["agent_states"]["jo"] == ["IA", "TX"]
    assert prepared["agent_states"]["tony-aca"] == ["CT", "IA"]
    assert {"jo", "matthew", "ali"}.issubset(prepared["agencies"]["summit"])


def test_confirmed_customer_mapping_requires_expected_agency(session, settings, tmp_path, monkeypatch):
    summit = Agency(slug="summit", name="Summit")
    agent = Agent(slug="matthew", name="Matthew", agency=summit)
    session.add_all([summit, agent])
    session.flush()
    user_id = uuid.uuid4()
    monkeypatch.setattr(
        "jawnix_data.customer_mappings._auth_users",
        lambda _: {"matthew@example.com": {"id": str(user_id), "email": "matthew@example.com"}},
    )
    path = tmp_path / "mappings.csv"
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=["customer_email", "agent_slug", "agency_slug", "confirmed"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "customer_email": "matthew@example.com",
                "agent_slug": "matthew",
                "agency_slug": "summit",
                "confirmed": "true",
            }
        )

    result = provision_customer_mappings(session, settings, path)
    profile = session.get(CustomerProfile, user_id)
    assert result["profilesCreated"] == 1
    assert profile.agent_id == agent.id
    assert profile.mapping_confirmed_at is not None
