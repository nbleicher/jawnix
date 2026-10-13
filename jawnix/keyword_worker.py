"""Independent processes for keyword activation and reserve generation."""

from __future__ import annotations

import asyncio
import argparse
import logging
import time

from .config import get_settings
from .database import SessionLocal
from .keyword_rollover import refill_keyword_reserve, run_automatic_keyword_rollover

log = logging.getLogger("jawnix.keyword-worker")
POLL_SECONDS = 5


def run(lane: str = "activate") -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    settings = get_settings()
    log.info("Keyword %s process started", lane)
    while True:
        if settings.scraper_ops_url:
            try:
                if lane == "refill":
                    result = asyncio.run(refill_keyword_reserve(SessionLocal, settings))
                else:
                    with SessionLocal.begin() as session:
                        result = asyncio.run(
                            run_automatic_keyword_rollover(session, settings)
                        )
                if result["outcome"] not in {"idle", "cooldown", "locked", "pooling"}:
                    log.info("Keyword %s outcome: %s", lane, result["outcome"])
            except Exception:
                log.exception("Automatic keyword rollover check failed")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lane", choices=("activate", "refill"), default="activate")
    run(parser.parse_args().lane)
