import asyncio
import uuid
from types import SimpleNamespace

from fastapi import HTTPException

from jawnix.request_updates import RequestUpdates, request_events


def test_push_is_scoped_coalesced_and_removed_on_disconnect():
    async def check():
        hub = RequestUpdates("sqlite://")
        async with hub.subscribe({"owned"}) as subscriber:
            hub.publish("someone-else")
            assert not subscriber.changed.is_set()
            hub.publish("owned")
            hub.publish("owned")
            assert subscriber.pending == {"owned"}
        assert not hub.subscribers
    asyncio.run(check())


def test_stream_rechecks_authorization_before_publishing():
    async def check():
        hub = RequestUpdates("sqlite://")
        hub.connected = True
        async def is_disconnected(): return False
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(request_updates=hub)), is_disconnected=is_disconnected)
        calls = 0
        async def authorize():
            nonlocal calls
            calls += 1
            if calls > 1: raise HTTPException(401, "Expired")
        stream = request_events(request, {"owned"}, authorize)
        assert "event: ready" in await anext(stream)
        hub.publish("owned")
        assert "event: expired" in await anext(stream)
        await stream.aclose()
        assert not hub.subscribers
    asyncio.run(check())


def test_customer_detail_and_stream_scope_do_not_expose_another_customer(session, monkeypatch):
    from test_customer_requests import _authenticate, _customer
    from conftest import make_request
    from jawnix.api import app
    _, _, profile = _customer(session, licensed_states=["TX"])
    _, other_customer, _ = _customer(session, licensed_states=["TX"])
    other = make_request(session, other_customer, 1)
    owned = make_request(session, profile.customer, 1)
    # The workspace is scoped to the current User Account.
    owned.user_id = profile.user_id
    session.commit()
    captured = []
    async def finite_stream(request, scope, authorize):
        captured.extend(scope)
        yield "event: ready\ndata: {}\n\n"
    monkeypatch.setattr("jawnix.api.request_events", finite_stream)
    client = _authenticate(session, profile.user_id)
    try:
        assert client.get(f"/api/me/batch-requests/{other.id}").status_code == 404
        assert client.get(f"/api/me/batch-requests/{owned.id}").status_code == 200
        assert client.get("/api/me/batch-requests/events").status_code == 200
        assert captured == [str(owned.id)]
    finally:
        app.dependency_overrides.clear()
