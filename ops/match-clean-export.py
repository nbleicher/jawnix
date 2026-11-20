"""Match a Textro clean export against Jawnix inventory and split it into per-state upload files.

Runs in the api container in a read-only transaction:
    docker compose run --rm -v /path/to/exports:/exports:ro api python /app/ops/match-clean-export.py \
        /exports/clean.csv --output-dir /exports/matched --scrubbed-on 2026-11-19

Title and state come from DB1 first, then DB2 (a Lead Correction outranks the
upload row), then the export's own Company and State columns. The scrub date
comes from the export when it carries a Scrubbed On column, otherwise from
--scrubbed-on (the Textro import date). Writes one clean-<STATE>.csv per state
with Phone, Company, State and Scrubbed On columns, plus rejects.csv for
duplicates, numbers that won't normalize and rows with no resolvable state.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import date
from pathlib import Path

from sqlalchemy import select, text

csv.field_size_limit(16 * 1024 * 1024)

CHUNK = 2000
STATE_HEADERS = {"state", "statecode", "region"}
SCRUB_DATE_HEADERS = {"scrubbedon", "scrubdate", "scrubbed", "scrubbedat", "scrubdateon"}
OUTPUT_HEADERS = ["Phone", "Company", "State", "Scrubbed On"]


def _normalized_headers(fields: list[str]) -> dict[str, str]:
    return {name: re.sub(r"[^a-z0-9]", "", name.lower()) for name in fields}


def _pick_column(fields: list[str], normalized: dict[str, str], candidates: set[str]) -> str | None:
    return next((name for name in fields if normalized[name] in candidates), None)


def _scrub_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Match a Textro clean export and split it into per-state upload files.")
    parser.add_argument("export", type=Path, help="Textro clean export CSV")
    parser.add_argument("--output-dir", type=Path, help="Directory for clean-<STATE>.csv files (default: <export>-matched/)")
    parser.add_argument("--scrubbed-on", help="Scrub date YYYY-MM-DD when the export carries none (the Textro import date)")
    args = parser.parse_args()

    from jawnix.database import SessionLocal
    from jawnix.lead_databases import db2_listings
    from jawnix.lead_uploads import PHONE_HEADERS, TITLE_HEADERS
    from jawnix.models import Lead, LeadDatabaseMembership
    from jawnix.states import US_STATES, US_STATE_OPTIONS, normalize_phone

    state_names = {name.lower(): code for code, name in US_STATE_OPTIONS}

    default_scrubbed_on = None
    if args.scrubbed_on:
        default_scrubbed_on = _scrub_date(args.scrubbed_on)
        if default_scrubbed_on is None:
            print("--scrubbed-on must be a YYYY-MM-DD date.", file=sys.stderr)
            return 2

    output_dir = args.output_dir or args.export.with_suffix("").parent / f"{args.export.stem}-matched"
    output_dir.mkdir(parents=True, exist_ok=True)
    rejects_path = output_dir / "rejects.csv"

    with args.export.open(encoding="utf-8-sig", newline="") as source:
        sample = source.read(65536)
        source.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(source, dialect=dialect)
        fields = reader.fieldnames or []
        normalized = _normalized_headers(fields)
        phone_column = _pick_column(fields, normalized, PHONE_HEADERS)
        title_column = _pick_column(fields, normalized, TITLE_HEADERS)
        state_column = _pick_column(fields, normalized, STATE_HEADERS)
        scrub_column = _pick_column(fields, normalized, SCRUB_DATE_HEADERS)
        if phone_column is None:
            print("The CSV must contain a Phone or Phone Number column.", file=sys.stderr)
            return 2
        if scrub_column is None and default_scrubbed_on is None:
            print("The export carries no Scrubbed On column; pass --scrubbed-on with the Textro import date.", file=sys.stderr)
            return 2

        writers: dict[str, csv.writer] = {}
        handles = []

        def state_writer(state: str) -> csv.writer:
            writer = writers.get(state)
            if writer is None:
                handle = (output_dir / f"clean-{state}.csv").open("w", encoding="utf-8", newline="")
                handles.append(handle)
                writer = csv.writer(handle)
                writer.writerow(OUTPUT_HEADERS)
                writers[state] = writer
            return writer

        def resolve_state(value: str) -> str:
            candidate = value.strip().upper()
            if candidate in US_STATES:
                return candidate
            return state_names.get(value.strip().lower(), "")

        processed = matched = rejected = 0
        rejects = rejects_path.open("w", encoding="utf-8", newline="")
        report = csv.writer(rejects)
        report.writerow(["row", "phone", "reason"])

        def reject(number: int, raw_phone: str, reason: str) -> None:
            nonlocal rejected
            rejected += 1
            safe_phone = "'" + raw_phone if raw_phone.startswith(("=", "+", "-", "@")) else raw_phone
            report.writerow([number, safe_phone, reason])

        try:
            with SessionLocal() as session:
                session.execute(text("SET TRANSACTION READ ONLY"))
                pending: list[tuple[int, str, str, str, date | None]] = []
                seen: set[str] = set()

                def flush() -> None:
                    nonlocal matched
                    if not pending:
                        return
                    phones = [row[1] for row in pending]
                    leads = {lead.phone: lead for lead in session.scalars(select(Lead).where(Lead.phone.in_(phones)))}
                    db2_ids = [
                        lead.id for lead in leads.values()
                        if not lead.in_db1 and lead.active_correction_id is None
                    ]
                    memberships = {
                        row.lead_id: row for row in session.scalars(
                            select(LeadDatabaseMembership).where(LeadDatabaseMembership.lead_id.in_(db2_ids))
                        )
                    } if db2_ids else {}
                    listings = db2_listings(session, [
                        (lead_id, membership.upload_id) for lead_id, membership in memberships.items()
                    ]) if memberships else {}
                    for number, phone, company, state, scrubbed_on in pending:
                        lead = leads.get(phone)
                        title, resolved = "", ""
                        if lead is not None:
                            if lead.in_db1 or lead.active_correction_id is not None:
                                title, resolved = lead.title, lead.state
                            else:
                                membership = memberships.get(lead.id)
                                listing = listings.get(lead.id)
                                if listing is not None:
                                    title, resolved = listing.title, listing.state
                                if not resolved and membership is not None:
                                    resolved = membership.state
                        title = title or company
                        resolved = resolve_state(resolved) or resolve_state(state)
                        if not resolved:
                            reject(number, phone, "State could not be determined.")
                            continue
                        matched += 1
                        state_writer(resolved).writerow([phone, title, resolved, scrubbed_on.isoformat()])
                    pending.clear()

                for number, row in enumerate(reader, start=2):
                    processed += 1
                    raw_phone = row.get(phone_column) or ""
                    phone = normalize_phone(raw_phone)
                    if phone is None:
                        reject(number, raw_phone, "Phone must normalize to a ten-digit US number.")
                        continue
                    if phone in seen:
                        reject(number, raw_phone, "Duplicate phone in export.")
                        continue
                    scrubbed_on = default_scrubbed_on
                    if scrub_column is not None:
                        scrubbed_on = _scrub_date(row.get(scrub_column) or "")
                        if scrubbed_on is None:
                            reject(number, raw_phone, "Scrub date must be a YYYY-MM-DD date.")
                            continue
                    seen.add(phone)
                    company = str(row.get(title_column) or "") if title_column else ""
                    state = str(row.get(state_column) or "") if state_column else ""
                    pending.append((number, phone, company, state, scrubbed_on))
                    if len(pending) >= CHUNK:
                        flush()
                flush()
        finally:
            for handle in handles:
                handle.close()
            rejects.close()

    counts = {path.stem.removeprefix("clean-"): sum(1 for _ in path.open(encoding="utf-8")) - 1
              for path in sorted(output_dir.glob("clean-*.csv"))}
    print(json.dumps({
        "processed": processed,
        "matched": matched,
        "rejected": rejected,
        "states": counts,
        "outputDir": str(output_dir),
        "rejects": str(rejects_path) if rejected else None,
    }, indent=2))
    if rejected:
        print(f"{rejected} rejected row(s) — review {rejects_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
