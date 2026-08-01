from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from jawnix.config import Settings, get_settings
from jawnix.database import SessionLocal
from jawnix.maintenance import expire_batch_files, purge_retained_records
from jawnix.models import NightlyReview

from jawnix.keyword_rollover import run_automatic_keyword_rollover
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


def run() -> None:
    settings = get_settings()
    cleaned_date = None
    while True:
        if settings.scraper_ops_url:
            try:
                with SessionLocal.begin() as session:
                    rollover = asyncio.run(
                        run_automatic_keyword_rollover(session, settings)
                    )
                if rollover["outcome"] not in {"idle", "cooldown", "locked"}:
                    log.info(
                        "Automatic keyword rollover outcome: %s",
                        rollover["outcome"],
                    )
            except Exception:
                log.exception("Automatic keyword rollover check failed")
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
        if cleaned_date != datetime.now(timezone.utc).date():
            try:
                with SessionLocal.begin() as session:
                    artifacts = expire_batch_files(session, settings)
                    records = purge_retained_records(session)
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
