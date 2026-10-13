"""Independent keyword handoff checks, with generation's existing cooldowns."""
from __future__ import annotations

import asyncio
import logging
import time

from .config import get_settings
from .database import SessionLocal
from .keyword_rollover import run_automatic_keyword_rollover

log = logging.getLogger("jawnix.keyword-worker")
POLL_SECONDS = 5


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    while True:
        if settings.scraper_ops_url:
            try:
                with SessionLocal.begin() as session:
                    result = asyncio.run(run_automatic_keyword_rollover(session, settings))
                if result["outcome"] not in {"idle", "cooldown", "locked", "pooling"}:
                    log.info("Automatic keyword rollover outcome: %s", result["outcome"])
            except Exception:
                log.exception("Automatic keyword rollover check failed")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    run()
