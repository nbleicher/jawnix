from __future__ import annotations

import json
from pathlib import Path

from jawnix_data.configuration import prepare_agent_config


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


def test_backup_covers_postgresql_and_persistent_scraper_dataset():
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    backup = Path("ops/backup.sh").read_text(encoding="utf-8")
    restore = Path("ops/restore.sh").read_text(encoding="utf-8")

    assert "- scraper_data:/data:ro" in compose
    assert "scraper_data=/data/health_leads/data" in backup
    assert 'scraper_dataset="$scraper_data/leads.db"' in backup
    assert 'scraper_metadata="$scraper_data/dataset-metadata.json"' in backup
    assert 'restic backup --tag scraper-dataset "$scraper_data"' in backup
    assert "JAWNIX_SCRAPER_RESTORE_PATH" in restore
    assert "scraper-dataset.sha256" in backup
    assert "restic forget --group-by tags --keep-within 14d --prune" in backup
