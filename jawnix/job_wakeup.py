"""Wake idle workers after commit, with bounded polling as a recovery path."""
import logging
import time

import psycopg
from sqlalchemy.engine import make_url

from .db_notifications import JOB_CHANNEL

log = logging.getLogger(__name__)


def postgres_dsn(database_url: str) -> str:
    return make_url(database_url).set(drivername="postgresql").render_as_string(hide_password=False)


class JobWakeup:
    def __init__(self, settings):
        self.lane = settings.worker_lane
        self.timeout = settings.worker_poll_seconds
        self.dsn = postgres_dsn(settings.database_url) if settings.database_url.startswith("postgresql") else None
        self.connection = None

    def connect(self):
        if self.dsn and self.connection is None:
            try:
                self.connection = psycopg.connect(self.dsn, autocommit=True, connect_timeout=5)
                self.connection.execute(f"LISTEN {JOB_CHANNEL}")
            except psycopg.Error as error:
                self.close()
                log.warning("Worker wakeup unavailable (%s)", type(error).__name__)

    def wait(self):
        self.connect()
        if self.connection is None:
            time.sleep(self.timeout)
            return
        deadline = time.monotonic() + self.timeout
        try:
            while time.monotonic() < deadline:
                for notification in self.connection.notifies(timeout=max(0, deadline-time.monotonic()), stop_after=1):
                    if self.lane == "default" or notification.payload == self.lane:
                        return
        except psycopg.Error as error:
            self.close()
            log.warning("Worker wakeup interrupted (%s)", type(error).__name__)

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None
