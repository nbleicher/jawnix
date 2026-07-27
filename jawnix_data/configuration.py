from __future__ import annotations

import json
from pathlib import Path

from jawnix.states import US_STATES


def prepare_agent_config(source: Path, destination: Path, overrides_path: Path) -> dict:
    if source.resolve() == destination.resolve():
        raise ValueError("The source agent config is read-only; choose a separate staging destination.")
    config = json.loads(source.read_text(encoding="utf-8"))
    overrides = json.loads(overrides_path.read_text(encoding="utf-8"))
    corrections = {
        str(source_code).strip().upper(): str(target_code).strip().upper()
        for source_code, target_code in (overrides.get("state_corrections") or {}).items()
    }
    invalid_targets = sorted({value for value in corrections.values() if value not in US_STATES})
    if invalid_targets:
        raise ValueError("Invalid state correction targets: " + ", ".join(invalid_targets))

    correction_counts = {source_code: 0 for source_code in corrections}
    for slug, states in (config.get("agent_states") or {}).items():
        corrected: list[str] = []
        for raw_state in states:
            state = str(raw_state).strip().upper()
            if state in corrections:
                correction_counts[state] += 1
                state = corrections[state]
            if state not in US_STATES:
                raise ValueError(f"Agent {slug} still has an invalid state after overrides: {state}")
            if state not in corrected:
                corrected.append(state)
        config["agent_states"][slug] = corrected

    agents = config.setdefault("agents", [])
    agencies = config.setdefault("agencies", {})
    additions: list[str] = []
    for item in overrides.get("agent_additions") or []:
        slug = str(item["slug"]).strip()
        agency = str(item["agency"]).strip()
        if not slug or not agency:
            raise ValueError("Agent additions require nonempty slug and agency values.")
        if slug not in agents:
            agents.append(slug)
            additions.append(slug)
        members = agencies.setdefault(agency, [])
        if slug not in members:
            members.append(slug)

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "source": str(source),
        "destination": str(destination),
        "stateCorrections": correction_counts,
        "agentsAdded": additions,
    }
