from __future__ import annotations

import csv
import sqlite3
import zipfile

from jawnix.config import Settings
from jawnix_data.nppes_collector import (
    CITY,
    CREDENTIAL,
    ENTITY,
    FIRST,
    LAST,
    MIDDLE,
    PHONE,
    STATE,
    TAXONOMY,
    USE_COLUMNS,
    ZIP,
    collect,
)
from jawnix_data.scraper import _prepare_nppes


class Response:
    text = '<a href="NPPES_Data_Dissemination_July_2026_V2.zip">download</a>'

    def raise_for_status(self):
        return None


def test_nppes_static_archive_is_versioned_before_refresh(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    current = data / "nppes.zip"
    current.write_bytes(b"old-version")
    settings = Settings(JAWNIX_SCRAPER_DB_PATH=data / "leads.db")
    monkeypatch.setattr("jawnix_data.scraper.httpx.get", lambda *_args, **_kwargs: Response())

    upstream, marker = _prepare_nppes(settings)

    assert upstream.endswith("NPPES_Data_Dissemination_July_2026_V2.zip")
    assert not current.exists()
    assert list((data / "nppes_versions").glob("legacy-*.zip"))[0].read_bytes() == b"old-version"
    marker.write_text(upstream + "\n")
    current.write_bytes(b"current-version")
    _prepare_nppes(settings)
    assert current.read_bytes() == b"current-version"


def test_nppes_collector_atomically_refreshes_only_nppes(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    database = data / "leads.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                niche TEXT NOT NULL,
                first_name TEXT,
                last_name TEXT,
                full_name TEXT,
                company TEXT,
                phone TEXT,
                state TEXT,
                city TEXT,
                zip TEXT,
                credential TEXT,
                raw_phone TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.executemany(
            "INSERT INTO leads (source, niche, phone, state) VALUES (?, ?, ?, ?)",
            [
                ("NPPES", "Old", "2125550000", "NY"),
                ("FMCSA", "Trucking", "3055550000", "FL"),
            ],
        )

    csv_path = tmp_path / "npidata_pfile_test.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=USE_COLUMNS)
        writer.writeheader()
        writer.writerow(
            {
                ENTITY: "1",
                LAST: "DOE",
                FIRST: "JANE",
                MIDDLE: "Q",
                CREDENTIAL: "NP",
                CITY: "AUSTIN",
                STATE: "TX",
                ZIP: "78701-1234",
                PHONE: "+1 (512) 555-0100",
                TAXONOMY: "363L00000X",
            }
        )
        writer.writerow(
            {
                ENTITY: "2",
                LAST: "COMPANY",
                FIRST: "",
                MIDDLE: "",
                CREDENTIAL: "",
                CITY: "AUSTIN",
                STATE: "TX",
                ZIP: "78701",
                PHONE: "5125559999",
                TAXONOMY: "",
            }
        )
    archive = data / "nppes.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.write(csv_path, arcname=csv_path.name)

    result = collect(database, archive, "https://example.test/nppes-v2.zip")
    assert result["inserted"] == 1
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT source, niche, phone, state, city, zip FROM leads ORDER BY source"
        ).fetchall()
    assert rows == [
        ("FMCSA", "Trucking", "3055550000", "FL", None, None),
        ("NPPES", "Nurse Practitioner", "5125550100", "TX", "Austin", "78701"),
    ]
    assert collect(database, archive, "https://example.test/nppes-v2.zip")["skipped"] is True
