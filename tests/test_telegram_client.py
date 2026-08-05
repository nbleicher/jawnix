"""TelegramClient transport classification (# telegram reliability)."""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest

from jawnix.config import Settings
from jawnix.telegram import (
    TelegramClient,
    TelegramEditTargetMissingError,
    TelegramTransientError,
    telegram_retry_delay,
)


def _client() -> TelegramClient:
    return TelegramClient(
        Settings(
            TELEGRAM_BOT_TOKEN="test-token",
            TELEGRAM_CHAT_ID="12345",
        )
    )


def _response(
    status_code: int,
    *,
    ok: bool = False,
    description: str = "",
    parameters: dict | None = None,
    text: str = "",
    json_raises: bool = False,
) -> httpx.Response:
    body: dict = {"ok": ok}
    if description:
        body["description"] = description
    if parameters is not None:
        body["parameters"] = parameters
    request = httpx.Request("POST", "https://api.telegram.org/bottest/sendMessage")
    if json_raises:
        # Non-JSON bodies still classify by status code.
        return httpx.Response(status_code, text=text or "<html>bad gateway</html>", request=request)
    return httpx.Response(status_code, json=body, request=request)


def test_telegram_retry_delay_schedule():
    assert telegram_retry_delay(1) == timedelta(seconds=30)
    assert telegram_retry_delay(2) == timedelta(seconds=60)
    assert telegram_retry_delay(3) == timedelta(seconds=300)
    assert telegram_retry_delay(4) == timedelta(seconds=900)
    assert telegram_retry_delay(5) == timedelta(seconds=1800)
    assert telegram_retry_delay(10) == timedelta(seconds=1800)
    assert telegram_retry_delay(0) == timedelta(seconds=30)


def test_network_error_is_transient(monkeypatch):
    def boom(*_args, **_kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr("jawnix.telegram.httpx.post", boom)
    with pytest.raises(TelegramTransientError, match="transport failed"):
        _client()._call("sendMessage", {"chat_id": "1", "text": "hi"})


def test_500_non_json_body_is_transient(monkeypatch):
    monkeypatch.setattr(
        "jawnix.telegram.httpx.post",
        lambda *_a, **_k: _response(502, text="<html>bad gateway</html>", json_raises=True),
    )
    with pytest.raises(TelegramTransientError, match="failed"):
        _client()._call("sendMessage", {"chat_id": "1", "text": "hi"})


def test_429_honors_retry_after(monkeypatch):
    monkeypatch.setattr(
        "jawnix.telegram.httpx.post",
        lambda *_a, **_k: _response(
            429,
            description="Too Many Requests: retry after 7",
            parameters={"retry_after": 7},
        ),
    )
    with pytest.raises(TelegramTransientError) as excinfo:
        _client()._call("sendMessage", {"chat_id": "1", "text": "hi"})
    assert excinfo.value.retry_after == timedelta(seconds=7)


def test_429_caps_retry_after_at_one_hour(monkeypatch):
    monkeypatch.setattr(
        "jawnix.telegram.httpx.post",
        lambda *_a, **_k: _response(
            429,
            description="Too Many Requests",
            parameters={"retry_after": 10_000},
        ),
    )
    with pytest.raises(TelegramTransientError) as excinfo:
        _client()._call("sendMessage", {"chat_id": "1", "text": "hi"})
    assert excinfo.value.retry_after == timedelta(seconds=3600)


def test_400_chat_not_found_is_permanent(monkeypatch):
    monkeypatch.setattr(
        "jawnix.telegram.httpx.post",
        lambda *_a, **_k: _response(400, description="Bad Request: chat not found"),
    )
    with pytest.raises(RuntimeError) as excinfo:
        _client()._call("sendMessage", {"chat_id": "1", "text": "hi"})
    assert not isinstance(excinfo.value, TelegramTransientError)
    assert "chat not found" in str(excinfo.value)


def test_edit_target_missing_is_typed(monkeypatch):
    monkeypatch.setattr(
        "jawnix.telegram.httpx.post",
        lambda *_a, **_k: _response(
            400,
            description="Bad Request: message to edit not found",
        ),
    )
    with pytest.raises(TelegramEditTargetMissingError):
        _client()._call(
            "editMessageText",
            {"chat_id": "1", "message_id": 9, "text": "hi"},
        )


def test_edit_target_missing_only_applies_to_edit(monkeypatch):
    monkeypatch.setattr(
        "jawnix.telegram.httpx.post",
        lambda *_a, **_k: _response(
            400,
            description="Bad Request: message to edit not found",
        ),
    )
    with pytest.raises(RuntimeError) as excinfo:
        _client()._call("sendMessage", {"chat_id": "1", "text": "hi"})
    assert not isinstance(excinfo.value, TelegramEditTargetMissingError)


def test_message_not_modified_is_tolerated(monkeypatch):
    monkeypatch.setattr(
        "jawnix.telegram.httpx.post",
        lambda *_a, **_k: _response(
            400,
            description="Bad Request: message is not modified",
        ),
    )
    data = _client()._call(
        "editMessageText",
        {"chat_id": "1", "message_id": 9, "text": "hi"},
    )
    assert data.get("ok") is False


def test_unconfigured_token_is_permanent():
    client = TelegramClient(Settings(TELEGRAM_BOT_TOKEN="", TELEGRAM_CHAT_ID="1"))
    with pytest.raises(RuntimeError, match="not configured"):
        client._call("sendMessage", {"chat_id": "1", "text": "hi"})
