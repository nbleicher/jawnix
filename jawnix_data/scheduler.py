from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone

from jawnix.config import Settings, get_settings
from jawnix.database import SessionLocal
from jawnix.clean_inventory import EXPIRE_CLEAN_INVENTORY_JOB
from jawnix.inventory_snapshots import enqueue_inventory_refresh
from jawnix.jobs import enqueue_job
from jawnix.maintenance import expire_batch_files, purge_retained_records
from jawnix.models import NightlyReview

from jawnix.nightly import (
    activate_scheduled_scraper_configuration,
    run_scheduled_nightly_review,
)
from .scraper import run_nightly_attempt


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("jawnix.scheduler")


def seconds_until(hour_utc: int) -> float:
    now = datetime.now(timezone.utc)
    target = now.replace(hour=hour_utc, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def run_nightly_scraper(settings: Settings) -> NightlyReview:
    with SessionLocal.begin() as session:
        return run_nightly_attempt(session, settings)


def enqueue_nightly_inventory_refresh(
    settings: Settings,
    *,
    now: datetime,
    last_enqueued: date | None,
) -> date | None:
    """Queue the nightly usable-inventory refresh once per UTC day.

    Holds, suppressions, and Global Exclusions change usable inventory between
    syncs, so the snapshot is recounted every night even without a new
    dataset. Returns the date it last queued for.
    """
    if now.hour < settings.nightly_review_hour_utc or last_enqueued == now.date():
        return last_enqueued
    with SessionLocal.begin() as session:
        enqueue_inventory_refresh(session, reason="nightly")
    log.info("Queued the nightly usable-inventory refresh")
    return now.date()


def enqueue_nightly_clean_expiry(
    settings: Settings,
    *,
    now: datetime,
    last_enqueued: date | None,
) -> date | None:
    """Queue the nightly DB3 expiry sweep once per UTC day.

    Clean memberships expire 30 days after their scrub date; the sweep removes
    them and moves DB3-only phones to DB1.
    """
    if now.hour < settings.nightly_review_hour_utc or last_enqueued == now.date():
        return last_enqueued
    with SessionLocal.begin() as session:
        enqueue_job(session, EXPIRE_CLEAN_INVENTORY_JOB)
    log.info("Queued the nightly Clean inventory expiry")
    return now.date()


def run() -> None:
    settings = get_settings()
    cleaned_date = None
    inventory_refresh_date = None
    clean_expiry_date = None
    while True:
        try:
            now = datetime.now(timezone.utc)
            if now.hour >= settings.scraper_publication_hour_utc:
                with SessionLocal.begin() as session:
                    activated = (
                        activate_scheduled_scraper_configuration(
                            session,
                            settings,
                            as_of=now,
                        )
                    )
                if activated is not None:
                    log.info(
                        "Activated Scraper Configuration v%s",
                        activated.version,
                    )
            if now.hour >= settings.nightly_review_hour_utc:
                with SessionLocal.begin() as session:
                    review = run_scheduled_nightly_review(
                        session,
                        settings,
                        as_of=now,
                    )
                log.info(
                    "Nightly review %s has status %s",
                    review.id,
                    review.status,
                )
        except Exception:
            log.exception("Nightly review attempt failed")
        try:
            inventory_refresh_date = enqueue_nightly_inventory_refresh(
                settings,
                now=datetime.now(timezone.utc),
                last_enqueued=inventory_refresh_date,
            )
        except Exception:
            log.exception("Nightly usable-inventory refresh was not queued")
        try:
            clean_expiry_date = enqueue_nightly_clean_expiry(
                settings,
                now=datetime.now(timezone.utc),
                last_enqueued=clean_expiry_date,
            )
        except Exception:
            log.exception("Nightly Clean inventory expiry was not queued")
        if cleaned_date != datetime.now(timezone.utc).date():
            try:
                with SessionLocal.begin() as session:
                    artifacts = expire_batch_files(session, settings)
                    records = purge_retained_records(session, settings)
                log.info(
                    "Daily retention cleanup: artifacts=%s records=%s",
                    artifacts,
                    records,
                )
                cleaned_date = datetime.now(timezone.utc).date()
            except Exception:
                log.exception("Expired batch cleanup failed")
        time.sleep(60)


if __name__ == "__main__":
    run()
