#!/usr/bin/env python3
"""
shipper.py — spool → Postgres loader (runs ON the worker box, e.g. box1)
========================================================================
Workers write each finished job's results to a local NDJSON file in the spool
dir, then create a `<file>.done` marker. This shipper DRAINS the folder: for
every completed file it bulk-upserts the businesses into Postgres (deduped) and
moves the file to archive/. It is triggered event-style by a systemd `.path`
unit on the `.done` marker, with a `.timer` as a backstop.

Design rules that make it safe:
  • Only files with a `.done` marker are touched (never a half-written file).
  • It drains the WHOLE folder each run → idempotent and burst-proof, whether
    woken by one event or fifty, or by the backstop timer.
  • Re-loading a file is harmless (ON CONFLICT (dedup_key) DO NOTHING) and files
    are archived after a successful load, so nothing double-loads.
  • On a DB error the file is left in place (not archived) and the run exits
    non-zero → systemd/timer retries it later. Scraping never blocks on the DB.

NDJSON line format (written by the worker, see the Go patch):
  {"job_id":123,"keyword":"plumbers","state":"fl","cell":"27.1,-82.1","entry":{...raw gmaps entry...}}

Usage:
  DATABASE_URL=... python3 shipper.py --drain
  python3 shipper.py --drain --spool-dir /data/incoming --max-backlog 500
  python3 shipper.py --dry-run            # parse + count, no DB, no archive
"""

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

INSERT_SQL = """
INSERT INTO businesses
  (dedup_key, place_id, cid, title, phone, website, category, address,
   latitude, longitude, rating, review_count, emails, state, keyword, cell, source_job_id, raw)
VALUES %s
ON CONFLICT (dedup_key) DO UPDATE
   SET last_seen = NOW(), review_count = EXCLUDED.review_count, rating = EXCLUDED.rating
"""

RESULT_COUNT_SQL = """
INSERT INTO scrape_results (job_id, keyword, result_count, phone_count, created_at)
VALUES (%s, %s, %s, %s, NOW())
ON CONFLICT (job_id) DO UPDATE SET
  keyword = EXCLUDED.keyword,
  result_count = EXCLUDED.result_count,
  phone_count = EXCLUDED.phone_count,
  created_at = EXCLUDED.created_at
"""


@dataclass
class ParseResult:
    rows: list
    job_id: int | None
    keyword: str | None
    result_count: int
    bad_lines: list


class ParseFileError(Exception):
    def __init__(self, result: ParseResult):
        self.result = result
        super().__init__(
            f"{len(result.bad_lines)} malformed NDJSON line(s)"
        )


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def dedup_key(e: dict) -> str:
    pid = (e.get("place_id") or "").strip()
    if pid:
        return "pid:" + pid
    cid = (e.get("cid") or "").strip()
    if cid:
        return "cid:" + cid
    h = hashlib.md5(f"{norm(e.get('title'))}|{norm(e.get('phone'))}|{norm(e.get('address'))}".encode())
    return "tp:" + h.hexdigest()


def f2(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def i2(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def row_from_line(obj: dict):
    """Build the businesses tuple from one NDJSON object."""
    e = obj.get("entry", obj)
    emails = e.get("emails") if isinstance(e.get("emails"), list) else None
    return (
        dedup_key(e),
        e.get("place_id") or None, e.get("cid") or None, e.get("title"), e.get("phone"),
        e.get("web_site") or e.get("website"), e.get("category"), e.get("address"),
        f2(e.get("latitude")), f2(e.get("longtitude") or e.get("longitude")),
        f2(e.get("review_rating") or e.get("rating")), i2(e.get("review_count")),
        emails, obj.get("state"), obj.get("keyword"), obj.get("cell"),
        i2(obj.get("job_id")), json.dumps(e),
    )


def job_id_from_name(data_path: Path):
    match = re.match(r"results-(\d+)-", data_path.name)
    return i2(match.group(1)) if match else None


def parse_file(
    data_path: Path,
    tolerate_parse_errors: bool = False,
) -> ParseResult:
    rows_by_key = {}
    bad_lines = []
    job_id = job_id_from_name(data_path)
    keyword = None
    with open(data_path, encoding="utf-8", errors="replace") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
                if job_id is None:
                    job_id = i2(item.get("job_id"))
                if keyword is None:
                    keyword = item.get("keyword")
                row = row_from_line(item)
                rows_by_key[row[0]] = row
            except Exception as error:
                bad_lines.append((line_no, str(error)))
    result = ParseResult(
        rows=list(rows_by_key.values()),
        job_id=job_id,
        keyword=keyword,
        result_count=len(rows_by_key),
        bad_lines=bad_lines,
    )
    if bad_lines and not tolerate_parse_errors:
        raise ParseFileError(result)
    return result


def phone_count(rows: list) -> int:
    return sum(1 for row in rows if str(row[4] or "").strip())


def unique_dest(path: Path) -> Path:
    if not path.exists():
        return path
    suffix = f".{int(time.time())}.{os.getpid()}"
    return path.with_name(path.name + suffix)


def move_pair(
    data: Path,
    marker: Path,
    destination: Path,
) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    if data.exists():
        shutil.move(
            str(data),
            str(unique_dest(destination / data.name)),
        )
    if marker.exists():
        shutil.move(
            str(marker),
            str(unique_dest(destination / marker.name)),
        )


def archive_destination(archive_root: Path, data: Path) -> Path:
    """Shard archived results by UTC day to avoid ext4 directory exhaustion."""
    timestamp = data.stat().st_mtime if data.exists() else time.time()
    day = datetime.fromtimestamp(timestamp, timezone.utc)
    return archive_root / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}"


def main():
    ap = argparse.ArgumentParser(description="Spool → Postgres shipper",
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--spool-dir", default=os.environ.get("GMS_SPOOL_DIR", "/data/incoming"))
    ap.add_argument("--archive", default=None, help="default: <spool>/archive")
    ap.add_argument(
        "--quarantine",
        default=None,
        help="default: <spool>/quarantine",
    )
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL"))
    ap.add_argument("--max-backlog", type=int, default=1000,
                    help="warn if more than this many .done files are pending")
    ap.add_argument("--drain", action="store_true", help="process all pending files then exit")
    ap.add_argument("--dry-run", action="store_true", help="parse + count only; no DB, no archive")
    ap.add_argument(
        "--tolerate-parse-errors",
        action="store_true",
        help=(
            "load valid lines from malformed files instead of "
            "quarantining them"
        ),
    )
    args = ap.parse_args()

    spool = Path(args.spool_dir)
    archive = Path(
        args.archive
        or os.environ.get("GMS_ARCHIVE_DIR", "")
        or spool / "archive-v2"
    )
    quarantine = (
        Path(args.quarantine)
        if args.quarantine
        else spool / "quarantine"
    )
    markers = sorted(spool.glob("*.ndjson.done"))

    if not markers:
        print(f"  shipper: nothing to ship in {spool}")
        return 0
    if len(markers) > args.max_backlog:
        print(f"  ⚠ backlog: {len(markers)} files pending (> {args.max_backlog}) — DB behind?",
              file=sys.stderr)

    if args.dry_run:
        total = 0
        bad_files = 0
        for m in markers:
            data = m.with_suffix("")  # strip .done
            parsed = ParseResult([], None, None, 0, [])
            if data.exists():
                try:
                    parsed = parse_file(
                        data,
                        args.tolerate_parse_errors,
                    )
                except ParseFileError as error:
                    parsed = error.result
                    bad_files += 1
            n = parsed.result_count
            total += n
            print(
                f"  [dry-run] {data.name}: {n} rows"
                + (
                    f", {len(parsed.bad_lines)} malformed line(s)"
                    if parsed.bad_lines
                    else ""
                )
            )
        print(f"  [dry-run] {len(markers)} files, {total:,} business rows — no DB writes.")
        return (
            1
            if bad_files and not args.tolerate_parse_errors
            else 0
        )

    if not args.dsn:
        print("ERROR: no DSN. Set DATABASE_URL or pass --dsn.", file=sys.stderr)
        return 2

    import psycopg2
    from psycopg2.extras import execute_values
    archive.mkdir(parents=True, exist_ok=True)
    conn = psycopg2.connect(args.dsn)

    shipped_files = shipped_rows = failed = quarantined = 0
    try:
        for m in markers:
            data = m.with_suffix("")  # results-*.ndjson
            if not data.exists():
                m.unlink(missing_ok=True)  # stale marker
                continue
            try:
                parsed = parse_file(
                    data,
                    args.tolerate_parse_errors,
                )
            except ParseFileError as error:
                parsed = error.result
                quarantined += 1
                print(
                    f"  parse failed for {data.name}: "
                    f"{len(parsed.bad_lines)} malformed line(s) "
                    "— moved to quarantine",
                    file=sys.stderr,
                )
                for line_no, message in parsed.bad_lines[:5]:
                    print(
                        f"    line {line_no}: {message}",
                        file=sys.stderr,
                    )
                move_pair(data, m, quarantine)
                continue
            try:
                with conn.cursor() as cur:
                    if parsed.rows:
                        execute_values(
                            cur,
                            INSERT_SQL,
                            parsed.rows,
                            page_size=500,
                        )
                    if parsed.job_id is not None:
                        cur.execute(
                            RESULT_COUNT_SQL,
                            (
                                parsed.job_id,
                                parsed.keyword,
                                parsed.result_count,
                                phone_count(parsed.rows),
                            ),
                        )
                conn.commit()
                # archive both files only after a successful commit
                move_pair(data, m, archive_destination(archive, data))
                shipped_files += 1
                shipped_rows += parsed.result_count
            except Exception as e:
                conn.rollback()
                failed += 1
                print(f"  load failed for {data.name}: {e} — left for retry", file=sys.stderr)
    finally:
        conn.close()

    print(f"  shipper: {shipped_files} file(s), {shipped_rows:,} rows → businesses"
          + (f"; {failed} left for retry" if failed else "")
          + (f"; {quarantined} quarantined" if quarantined else ""))
    return 1 if failed or quarantined else 0


if __name__ == "__main__":
    sys.exit(main())
