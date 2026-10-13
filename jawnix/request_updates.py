"""One PostgreSQL listener per API process fans out committed request changes."""
import asyncio
import json
import logging
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field

import psycopg
from fastapi import HTTPException

from .config import get_settings
from .db_notifications import REQUEST_CHANNEL
from .job_wakeup import postgres_dsn

log = logging.getLogger(__name__)


@dataclass(eq=False)
class Subscription:
    scope: set[str]
    pending: set[str] = field(default_factory=set)
    changed: asyncio.Event = field(default_factory=asyncio.Event)


class RequestUpdates:
    def __init__(self, database_url):
        self.database_url = database_url
        self.subscribers: set[Subscription] = set()
        self.connected = False
        self.task = None

    async def start(self):
        if self.database_url.startswith("postgresql"):
            self.task = asyncio.create_task(self._listen())

    async def stop(self):
        if self.task is not None:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
        self.connected = False

    async def _listen(self):
        while True:
            try:
                connection = await psycopg.AsyncConnection.connect(postgres_dsn(self.database_url), autocommit=True, connect_timeout=5)
                async with connection:
                    await connection.execute(f"LISTEN {REQUEST_CHANNEL}")
                    self.connected = True
                    async for notification in connection.notifies():
                        self.publish(notification.payload)
            except psycopg.Error as error:
                log.warning("Live request listener unavailable (%s)", type(error).__name__)
            finally:
                self.connected = False
            await asyncio.sleep(2)

    def publish(self, request_id: str):
        for subscriber in self.subscribers:
            if request_id in subscriber.scope:
                subscriber.pending.add(request_id)
                subscriber.changed.set()

    @asynccontextmanager
    async def subscribe(self, scope):
        subscriber = Subscription(set(scope))
        self.subscribers.add(subscriber)
        try:
            yield subscriber
        finally:
            self.subscribers.discard(subscriber)


@asynccontextmanager
async def request_updates_lifespan(app):
    hub = RequestUpdates(get_settings().database_url)
    app.state.request_updates = hub
    await hub.start()
    try:
        yield
    finally:
        await hub.stop()


async def request_events(request, scope, authorize):
    hub = request.app.state.request_updates
    async with hub.subscribe(scope) as subscription:
        connection_state = None
        while not await request.is_disconnected():
            # The same live authorization runs at connection, on each update,
            # and at least every heartbeat. No database connection is retained
            # while waiting for an event.
            try:
                await authorize()
            except HTTPException:
                yield "event: expired\ndata: {}\n\n"
                return
            if connection_state != hub.connected:
                connection_state = hub.connected
                event_name = "ready" if hub.connected else "fallback"
                yield f"event: {event_name}\ndata: {{}}\n\n"
                if subscription.pending:
                    continue
            if subscription.pending:
                ids = sorted(subscription.pending)
                subscription.pending.clear()
                subscription.changed.clear()
                yield "event: change\ndata: " + json.dumps({"requestIds":ids}) + "\n\n"
            try:
                await asyncio.wait_for(subscription.changed.wait(), timeout=15)
            except TimeoutError:
                yield ": keepalive\n\n"
