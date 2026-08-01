"""Versioned explicit Source Segment configuration."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

import yaml

STATE_PATTERN = re.compile(r"^[A-Za-z]{2}$")
STATUSES = {"active", "reduced", "paused"}


def normalize_keyword(value: str) -> str:
    return " ".join(value.strip().split()).casefold()


def normalize_state(value: str) -> str:
    state = value.strip().upper()
    if not STATE_PATTERN.fullmatch(state):
        raise ValueError("state must be a two-letter code")
    return state


def segment_identity(keyword: str, state: str) -> str:
    keyword = normalize_keyword(keyword)
    if not keyword:
        raise ValueError("keyword is required")
    return f"{normalize_state(state)}::{keyword}"


@dataclass(frozen=True)
class SourceSegment:
    id: str
    keyword: str
    state: str
    niche: str
    niche_confirmed: bool
    status: str
    cadence_multiplier: float
    version: int
    seed_segment_id: str | None = None

    @classmethod
    def from_mapping(cls, value: dict, version: int):
        keyword = normalize_keyword(str(value["keyword"]))
        state = normalize_state(str(value["state"]))
        identity = segment_identity(keyword, state)
        supplied_identity = str(value.get("id") or identity)
        if supplied_identity != identity:
            raise ValueError(f"Source Segment identity mismatch: {supplied_identity}")
        status = str(value.get("status") or "active").lower()
        if status not in STATUSES:
            raise ValueError(f"unsupported Source Segment status: {status}")
        cadence = float(
            value.get(
                "cadence_multiplier",
                0.5 if status == "reduced" else (0.0 if status == "paused" else 1.0),
            )
        )
        expected = {"active": 1.0, "reduced": 0.5, "paused": 0.0}[status]
        if cadence != expected:
            raise ValueError(f"{status} Source Segment cadence must be {expected}")
        return cls(
            id=identity,
            keyword=keyword,
            state=state,
            niche=str(value.get("niche") or "").strip(),
            niche_confirmed=bool(value.get("niche_confirmed", False)),
            status=status,
            cadence_multiplier=cadence,
            version=version,
            seed_segment_id=(
                str(value["seed_segment_id"])
                if value.get("seed_segment_id")
                else None
            ),
        )

    def runs_on(self, campaign_date: date) -> bool:
        if self.status == "paused":
            return False
        if self.status == "active":
            return True
        parity = int(hashlib.sha256(self.id.encode()).hexdigest()[:2], 16) % 2
        return campaign_date.toordinal() % 2 == parity


def load(path: Path) -> tuple[int, list[SourceSegment]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    version = int(data.get("version") or 0)
    if version < 1:
        raise ValueError("Source Segment version must be positive")
    segments = [
        SourceSegment.from_mapping(item, version)
        for item in data.get("segments") or []
    ]
    identities = [item.id for item in segments]
    if len(identities) != len(set(identities)):
        raise ValueError("Source Segment identities must be unique")
    return version, segments


def write(path: Path, version: int, segments: list[SourceSegment]) -> None:
    payload = {
        "schema_version": 1,
        "version": version,
        "segments": [
            {
                key: value
                for key, value in asdict(segment).items()
                if key != "version" and value is not None
            }
            for segment in sorted(segments, key=lambda item: item.id)
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            yaml.safe_dump(payload, handle, sort_keys=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def materialize_legacy(
    path: Path, *, states: list[str], keywords: list[str]
) -> tuple[int, list[SourceSegment]]:
    if path.exists():
        return load(path)
    segments = [
        SourceSegment(
            id=segment_identity(keyword, state),
            keyword=normalize_keyword(keyword),
            state=normalize_state(state),
            niche="",
            niche_confirmed=False,
            status="active",
            cadence_multiplier=1.0,
            version=1,
        )
        for state in states
        for keyword in keywords
    ]
    write(path, 1, segments)
    return load(path)


def contract_checksum(version: int, segments: list[SourceSegment]) -> str:
    return hashlib.sha256(
        json.dumps(
            {"version": version, "segments": [asdict(item) for item in segments]},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
