from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import zipfile
from pathlib import Path

import httpx
import pandas as pd


ENTITY = "Entity Type Code"
LAST = "Provider Last Name (Legal Name)"
FIRST = "Provider First Name"
MIDDLE = "Provider Middle Name"
CREDENTIAL = "Provider Credential Text"
CITY = "Provider Business Practice Location Address City Name"
STATE = "Provider Business Practice Location Address State Name"
ZIP = "Provider Business Practice Location Address Postal Code"
PHONE = "Provider Business Practice Location Address Telephone Number"
TAXONOMY = "Healthcare Provider Taxonomy Code_1"
USE_COLUMNS = [ENTITY, LAST, FIRST, MIDDLE, CREDENTIAL, CITY, STATE, ZIP, PHONE, TAXONOMY]

TITLES = {
    "111N00000X": "Chiropractor",
    "122300000X": "Dentist",
    "1223G0001X": "Dentist",
    "1223P0221X": "Dentist",
    "103T00000X": "Psychologist",
    "101Y00000X": "Mental Health Counselor",
    "106H00000X": "Marriage Family Therapist",
    "225100000X": "Physical Therapist",
    "225X00000X": "Occupational Therapist",
    "171100000X": "Acupuncturist",
    "152W00000X": "Optometrist",
    "207Q00000X": "Family Medicine Physician",
    "207R00000X": "Internal Medicine Physician",
    "208000000X": "Pediatrician",
    "207N00000X": "Dermatologist",
    "207L00000X": "Anesthesiologist",
    "207P00000X": "Emergency Medicine Physician",
    "204D00000X": "Neuromusculoskeletal",
    "213E00000X": "Podiatrist",
    "183500000X": "Pharmacist",
    "246ZB0301X": "Clinical Lab",
    "363L00000X": "Nurse Practitioner",
    "364S00000X": "Clinical Nurse Specialist",
    "367500000X": "Nurse Anesthetist",
    "374700000X": "Respiratory Therapist",
}


def _download(url: str, destination: Path) -> None:
    partial = destination.with_suffix(destination.suffix + ".part")
    partial.unlink(missing_ok=True)
    with httpx.stream("GET", url, timeout=httpx.Timeout(60, read=300), follow_redirects=True) as response:
        response.raise_for_status()
        with partial.open("wb") as handle:
            for chunk in response.iter_bytes(1024 * 1024):
                handle.write(chunk)
    partial.replace(destination)


def _snapshot_database(source: Path, destination: Path) -> None:
    destination.unlink(missing_ok=True)
    with sqlite3.connect(source) as current, sqlite3.connect(destination) as staged:
        current.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        current.backup(staged)
        staged.execute("PRAGMA journal_mode=DELETE")


def _normalized_chunk(chunk: pd.DataFrame) -> pd.DataFrame:
    chunk = chunk[chunk[ENTITY].fillna("").str.strip() == "1"].copy()
    digits = chunk[PHONE].fillna("").str.replace(r"\D", "", regex=True)
    valid = (digits.str.len() == 10) | ((digits.str.len() == 11) & digits.str.startswith("1"))
    chunk = chunk[valid].copy()
    if chunk.empty:
        return chunk
    chunk["_phone"] = digits[valid].str[-10:]
    chunk["_state"] = chunk[STATE].fillna("").str.strip().str.upper().str[:2]
    chunk = chunk[chunk["_state"].str.fullmatch(r"[A-Z]{2}")].copy()
    chunk["_first"] = chunk[FIRST].fillna("").str.strip().str.title()
    chunk["_last"] = chunk[LAST].fillna("").str.strip().str.title()
    middle = chunk[MIDDLE].fillna("").str.strip().str.title()
    chunk["_full"] = (
        chunk["_first"] + " " + middle + " " + chunk["_last"]
    ).str.replace(r"\s+", " ", regex=True).str.strip()
    chunk["_title"] = chunk[TAXONOMY].fillna("").map(TITLES).fillna("Healthcare Provider")
    return chunk


def collect(database: Path, archive: Path, source_url: str) -> dict:
    marker = database.parent / "nppes.source-url"
    previous_url = marker.read_text(encoding="utf-8").strip() if marker.is_file() else ""
    if previous_url == source_url and archive.is_file():
        return {"skipped": True, "reason": "NPPES upstream version unchanged", "sourceUrl": source_url}
    if not database.is_file():
        raise FileNotFoundError(f"Scraper database was not found: {database}")
    if not archive.is_file():
        _download(source_url, archive)

    staged = database.with_suffix(".next.db")
    _snapshot_database(database, staged)
    inserted = 0
    try:
        with zipfile.ZipFile(archive) as bundle:
            member = next(
                name
                for name in bundle.namelist()
                if Path(name).name.startswith("npidata_pfile")
                and name.lower().endswith(".csv")
                and "fileheader" not in name.lower()
            )
            with sqlite3.connect(staged) as connection, bundle.open(member) as csv_file:
                connection.execute("DELETE FROM leads WHERE UPPER(source) = 'NPPES'")
                connection.commit()
                for chunk in pd.read_csv(
                    csv_file,
                    dtype=str,
                    chunksize=100_000,
                    low_memory=False,
                    on_bad_lines="skip",
                    usecols=USE_COLUMNS,
                ):
                    normalized = _normalized_chunk(chunk)
                    if normalized.empty:
                        continue
                    normalized["_city"] = normalized[CITY].fillna("").str.strip().str.title()
                    normalized["_zip"] = normalized[ZIP].fillna("").str.strip().str[:5]
                    normalized["_credential"] = normalized[CREDENTIAL].fillna("").str.strip()
                    normalized["_raw_phone"] = normalized[PHONE].fillna("").str.strip()
                    values = normalized[
                        [
                            "_title",
                            "_first",
                            "_last",
                            "_full",
                            "_phone",
                            "_state",
                            "_city",
                            "_zip",
                            "_credential",
                            "_raw_phone",
                        ]
                    ].itertuples(index=False, name=None)
                    rows = (
                        ("NPPES", title, first, last, full, None, phone, state, city, zipcode, credential, raw_phone)
                        for title, first, last, full, phone, state, city, zipcode, credential, raw_phone in values
                    )
                    connection.executemany(
                        """
                        INSERT INTO leads (
                            source, niche, first_name, last_name, full_name, company,
                            phone, state, city, zip, credential, raw_phone
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        rows,
                    )
                    connection.commit()
                    inserted += len(normalized)
                connection.execute("PRAGMA optimize")
        for suffix in ("-wal", "-shm"):
            Path(f"{database}{suffix}").unlink(missing_ok=True)
        shutil.copymode(database, staged)
        staged.replace(database)
        marker.write_text(source_url + "\n", encoding="utf-8")
        return {"skipped": False, "inserted": inserted, "sourceUrl": source_url}
    except Exception:
        staged.unlink(missing_ok=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh NPPES records in the persistent scraper database.")
    parser.add_argument("--sources", nargs="+", default=["nppes"])
    args = parser.parse_args()
    unsupported = set(args.sources) - {"nppes"}
    if unsupported:
        parser.error(f"unsupported sources: {', '.join(sorted(unsupported))}")
    database = Path(os.environ.get("JAWNIX_SCRAPER_DB_PATH", "/data/health_leads/data/leads.db"))
    source_url = os.environ.get("JAWNIX_NPPES_SOURCE_URL", "").strip()
    if not source_url:
        parser.error("JAWNIX_NPPES_SOURCE_URL was not provided by the scheduler")
    result = collect(database, database.parent / "nppes.zip", source_url)
    print(result)


if __name__ == "__main__":
    main()
