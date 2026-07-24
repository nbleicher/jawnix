from __future__ import annotations

import hmac
import uuid

import httpx

from .config import Settings
from .models import LeadRequest


ACTION_PREFIX = "jawnix"
ALLOWED_ACTIONS = {"approve", "reject", "retry", "retry_delivery"}


def verify_telegram_secret(provided: str, expected: str) -> bool:
    if not provided or not expected:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def callback_data(action: str, request_id: uuid.UUID) -> str:
    if action not in ALLOWED_ACTIONS:
        raise ValueError(f"Unsupported Telegram action: {action}")
    value = f"{ACTION_PREFIX}:{action}:{request_id}"
    if len(value.encode("utf-8")) > 64:
        raise ValueError("Telegram callback data exceeds 64 bytes.")
    return value


def parse_callback_data(value: str) -> tuple[str, uuid.UUID]:
    try:
        prefix, action, raw_request_id = value.split(":", 2)
        request_id = uuid.UUID(raw_request_id)
    except (ValueError, AttributeError):
        raise ValueError("Malformed Telegram callback data.") from None
    if prefix != ACTION_PREFIX or action not in ALLOWED_ACTIONS:
        raise ValueError("Unsupported Telegram callback data.")
    return action, request_id


def _request_text(request: LeadRequest) -> str:
    customer = request.profile
    agent = request.agent
    name = " ".join(part for part in (customer.first_name, customer.last_name) if part).strip() or customer.email
    lines = [
        "Jawnix batch request",
        "",
        f"Customer: {name}",
        f"Email: {customer.email}",
        f"Agent: {agent.name}",
        f"Rows: {request.lead_count:,}",
        f"States: {', '.join(request.states_snapshot)}",
        f"Status: {request.status.replace('_', ' ').title()}",
    ]
    if request.available_count is not None:
        lines.append(f"Available: {request.available_count:,}")
    if request.status_message:
        lines.extend(("", request.status_message[:3000]))
    lines.extend(("", f"Request: {request.id}"))
    return "\n".join(lines)


def _keyboard(request: LeadRequest) -> dict:
    rows: list[list[dict[str, str]]] = []
    if request.status == "pending":
        rows = [
            [
                {"text": "Approve", "callback_data": callback_data("approve", request.id)},
                {"text": "Reject", "callback_data": callback_data("reject", request.id)},
            ]
        ]
    elif request.status == "waiting_inventory":
        rows = [
            [
                {"text": "Retry", "callback_data": callback_data("retry", request.id)},
                {"text": "Reject", "callback_data": callback_data("reject", request.id)},
            ]
        ]
    elif request.status == "failed":
        action = "retry_generation" if request.artifact is None else "retry_delivery"
        if action == "retry_generation":
            rows = [[{"text": "Retry generation", "callback_data": callback_data("retry", request.id)}]]
        else:
            rows = [[{"text": "Retry delivery", "callback_data": callback_data("retry_delivery", request.id)}]]
    return {"inline_keyboard": rows}


class TelegramClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _call(self, method: str, payload: dict, timeout: float = 20) -> dict:
        if not self.settings.telegram_bot_token:
            raise RuntimeError("Telegram is not configured.")
        response = httpx.post(
            f"https://api.telegram.org/bot{self.settings.telegram_bot_token}/{method}",
            json=payload,
            timeout=timeout,
        )
        data = response.json()
        if response.status_code != 200 or not data.get("ok"):
            raise RuntimeError(f"Telegram {method} failed: {data.get('description', response.text)}")
        return data

    def post_request(self, request: LeadRequest) -> tuple[str, str]:
        if not self.settings.telegram_chat_id:
            raise RuntimeError("TELEGRAM_CHAT_ID is not configured.")
        data = self._call(
            "sendMessage",
            {
                "chat_id": self.settings.telegram_chat_id,
                "text": _request_text(request),
                "reply_markup": _keyboard(request),
            },
        )
        result = data["result"]
        return str(result["chat"]["id"]), str(result["message_id"])

    def update_request(self, request: LeadRequest, chat_id: str, message_id: str) -> None:
        self._call(
            "editMessageText",
            {
                "chat_id": chat_id,
                "message_id": int(message_id),
                "text": _request_text(request),
                "reply_markup": _keyboard(request),
            },
        )

    def answer_callback(self, callback_query_id: str, text: str = "Queued") -> None:
        if not callback_query_id:
            return
        self._call(
            "answerCallbackQuery",
            {"callback_query_id": callback_query_id, "text": text[:200]},
            timeout=5,
        )
