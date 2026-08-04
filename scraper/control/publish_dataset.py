#!/usr/bin/env python3
"""Commit one replay-safe daily dataset marker after the scrape queue drains."""

from __future__ import annotations

import hashlib
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import psycopg2


LOCK_ID = 2_026_072_701
ACTIVE_STATES = (
    "available",
    "pending",
    "scheduled",
    "retryable",
    "running",
)


def publish(
    connection,
    *,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(timezone.utc)
    publication_date = now.date()
    connection.set_session(
        isolation_level="SERIALIZABLE",
        readonly=False,
    )
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT publication_date, committed_at, business_count,
                   lead_count, latest_job_at, checksum
            FROM scraper_dataset_publications
            WHERE publication_date = %s
            """,
            (publication_date,),
        )
        existing = cursor.fetchone()
        if existing:
            return {
                "publication_date": existing[0],
                "committed_at": existing[1],
                "business_count": int(existing[2]),
                "lead_count": int(existing[3]),
                "latest_job_at": existing[4],
                "checksum": existing[5],
            }
        cursor.execute(
            """
            SELECT
                count(*) FILTER (
                    WHERE kind = 'scrape' AND state::text = ANY(%s)
                ),
                max(finalized_at) FILTER (
                    WHERE kind = 'scrape' AND finalized_at IS NOT NULL
                )
            FROM river_job
            """,
            (list(ACTIVE_STATES),),
        )
        active_jobs, latest_job_at = cursor.fetchone()
        if int(active_jobs):
            raise RuntimeError(f"{int(active_jobs)} scrape jobs are still active")
        if latest_job_at is None or latest_job_at.date() != publication_date:
            raise RuntimeError("no completed scrape job exists for this UTC day")
        cursor.execute("SELECT count(*) FROM businesses")
        business_count = int(cursor.fetchone()[0])
        cursor.execute("SELECT count(*) FROM leads")
        lead_count = int(cursor.fetchone()[0])
        committed_at = now
        checksum = hashlib.sha256(
            (
                f"{publication_date.isoformat()}:{committed_at.isoformat()}:"
                f"{business_count}:{lead_count}:{latest_job_at.isoformat()}"
            ).encode()
        ).hexdigest()
        cursor.execute(
            """
            INSERT INTO scraper_dataset_publications (
                publication_date, committed_at, business_count,
                lead_count, latest_job_at, checksum
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                publication_date,
                committed_at,
                business_count,
                lead_count,
                latest_job_at,
                checksum,
            ),
        )
    connection.commit()
    return {
        "publication_date": publication_date,
        "committed_at": committed_at,
        "business_count": business_count,
        "lead_count": lead_count,
        "latest_job_at": latest_job_at,
        "checksum": checksum,
    }


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "")
    pause_path = Path(
        os.environ.get(
            "GMS_PIPELINE_PAUSE_FILE",
            str(Path(__file__).parent / "runtime" / "pipeline.paused"),
        )
    )
    timeout_seconds = int(
        os.environ.get("DATASET_PUBLICATION_DRAIN_TIMEOUT_SECONDS", "3300")
    )
    if not dsn:
        print("dataset-publication: DATABASE_URL is not configured", file=sys.stderr)
        return 1
    pause_path.parent.mkdir(parents=True, exist_ok=True)
    owned_pause = not pause_path.exists()
    if owned_pause:
        pause_path.write_text("daily dataset publication\n", encoding="utf-8")
    try:
        deadline = time.monotonic() + timeout_seconds
        while True:
            with psycopg2.connect(dsn) as connection:
                try:
                    result = publish(connection)
                except RuntimeError as error:
                    if time.monotonic() >= deadline:
                        print(f"dataset-publication: {error}", file=sys.stderr)
                        return 1
                else:
                    print(
                        "dataset-publication: committed "
                        f"{result['publication_date']} {result['checksum']}"
                    )
                    return 0
            time.sleep(15)
    finally:
        if owned_pause:
            pause_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
