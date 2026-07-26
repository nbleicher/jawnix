from __future__ import annotations

import hmac
import logging
import uuid

import httpx

from .config import Settings
from .models import (
    InventoryConflict,
    LeadRequest,
    NightlyReview,
    ScrapeAnomaly,
    ScraperRun,
)


logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

ACTION_PREFIX = "jawnix"
ANOMALY_PREFIX = "jawnix-a"
CONFLICT_PREFIX = "jawnix-c"
ALLOWED_ACTIONS = {"approve", "reject", "retry", "retry_delivery"}
ALLOWED_ANOMALY_ACTIONS = {"confirm", "deny"}
ALLOWED_CONFLICT_ACTIONS = {"confirm", "deny"}


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


def anomaly_callback_data(action: str, anomaly_id: uuid.UUID) -> str:
    if action not in ALLOWED_ANOMALY_ACTIONS:
        raise ValueError(f"Unsupported Scrape Anomaly action: {action}")
    value = f"{ANOMALY_PREFIX}:{action}:{anomaly_id}"
    if len(value.encode("utf-8")) > 64:
        raise ValueError("Telegram callback data exceeds 64 bytes.")
    return value


def parse_anomaly_callback_data(value: str) -> tuple[str, uuid.UUID]:
    try:
        prefix, action, raw_anomaly_id = value.split(":", 2)
        anomaly_id = uuid.UUID(raw_anomaly_id)
    except (ValueError, AttributeError):
        raise ValueError("Malformed Telegram anomaly callback data.") from None
    if (
        prefix != ANOMALY_PREFIX
        or action not in ALLOWED_ANOMALY_ACTIONS
    ):
        raise ValueError("Unsupported Telegram anomaly callback data.")
    return action, anomaly_id


def conflict_callback_data(action: str, conflict_id: uuid.UUID) -> str:
    if action not in ALLOWED_CONFLICT_ACTIONS:
        raise ValueError(f"Unsupported Inventory Conflict action: {action}")
    value = f"{CONFLICT_PREFIX}:{action}:{conflict_id}"
    if len(value.encode("utf-8")) > 64:
        raise ValueError("Telegram callback data exceeds 64 bytes.")
    return value


def parse_conflict_callback_data(value: str) -> tuple[str, uuid.UUID]:
    try:
        prefix, action, raw_conflict_id = value.split(":", 2)
        conflict_id = uuid.UUID(raw_conflict_id)
    except (ValueError, AttributeError):
        raise ValueError("Malformed Telegram conflict callback data.") from None
    if (
        prefix != CONFLICT_PREFIX
        or action not in ALLOWED_CONFLICT_ACTIONS
    ):
        raise ValueError("Unsupported Telegram conflict callback data.")
    return action, conflict_id


def _request_text(request: LeadRequest) -> str:
    customer = request.profile
    customer_identity = request.customer
    name = " ".join(part for part in (customer.first_name, customer.last_name) if part).strip() or customer.email
    lines = [
        "Jawnix batch request",
        "",
        f"Customer: {name}",
        f"Email: {customer.email}",
        f"Customer: {customer_identity.name}",
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
            description = str(data.get("description") or response.text)
            if method == "editMessageText" and "message is not modified" in description.lower():
                return data
            raise RuntimeError(f"Telegram {method} failed: {description}")
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

    def _nightly_review_message(
        self,
        review: NightlyReview,
        anomaly: ScrapeAnomaly | None = None,
    ) -> tuple[str, dict]:
        configuration = review.summary.get("configuration") or {}
        run = review.summary.get("run") or {}
        dataset = review.summary.get("dataset") or {}
        segments = review.summary.get("segments") or []
        inventory = review.summary.get("inventory") or {}
        waiting = review.summary.get("waitingRequests") or []
        conflicts = review.summary.get("inventoryConflicts") or []
        recommendations = review.summary.get("recommendations") or []
        failures = review.summary.get("failures") or []
        text = "\n".join(
            [
                "Jawnix Nightly Review",
                "",
                f"Configuration: v{configuration.get('version') or 'none'}",
                f"Run: {run.get('status') or 'unknown'}",
                f"Dataset: v{dataset.get('version') or 'none'} "
                f"({dataset.get('syncStatus') or 'not synchronized'})",
                f"Segments: {len(segments):,} "
                f"({sum(bool(item.get('anomalous')) for item in segments):,} anomalous)",
                f"Inventory: {int(inventory.get('total', 0)):,}",
                f"Waiting requests: {len(waiting):,}",
                f"Inventory conflicts: {len(conflicts):,}",
                f"Recommendations: {len(recommendations):,}",
                f"Failures: {len(failures):,}",
                "",
                f"Review: {self.settings.public_base_url}/admin.html#nightly-{review.id}",
            ]
        )
        keyboard = {"inline_keyboard": []}
        if anomaly is not None and anomaly.status == "pending":
            keyboard = {
                "inline_keyboard": [
                    [
                        {
                            "text": "Confirm staged dataset",
                            "callback_data": anomaly_callback_data(
                                "confirm",
                                anomaly.id,
                            ),
                        },
                        {
                            "text": "Deny",
                            "callback_data": anomaly_callback_data(
                                "deny",
                                anomaly.id,
                            ),
                        },
                    ]
                ]
            }
        return text, keyboard

    def post_nightly_review(
        self,
        review: NightlyReview,
        anomaly: ScrapeAnomaly | None = None,
    ) -> str:
        if not self.settings.telegram_chat_id:
            raise RuntimeError("TELEGRAM_CHAT_ID is not configured.")
        text, keyboard = self._nightly_review_message(review, anomaly)
        data = self._call(
            "sendMessage",
            {
                "chat_id": self.settings.telegram_chat_id,
                "text": text,
                "reply_markup": keyboard,
            },
        )
        return str(data["result"]["message_id"])

    def update_nightly_review(
        self,
        review: NightlyReview,
        anomaly: ScrapeAnomaly | None = None,
    ) -> None:
        text, keyboard = self._nightly_review_message(review, anomaly)
        self._call(
            "editMessageText",
            {
                "chat_id": self.settings.telegram_chat_id,
                "message_id": int(review.telegram_message_id),
                "text": text,
                "reply_markup": keyboard,
            },
        )

    def _anomaly_message(
        self,
        anomaly: ScrapeAnomaly,
        run: ScraperRun,
    ) -> tuple[str, dict]:
        segments = ", ".join(
            run.details.get("anomalousSegments") or []
        )
        text = "\n".join(
            [
                "Jawnix Scrape Anomaly",
                "",
                f"Run: {run.id}",
                f"Segments: {segments or 'Unknown'}",
                f"Checksum: {anomaly.dataset_checksum}",
                f"Status: {anomaly.status.replace('_', ' ').title()}",
                "",
                "Confirm publishes this exact staged dataset. "
                "Deny preserves the current dataset.",
            ]
        )
        keyboard = {"inline_keyboard": []}
        if anomaly.status == "pending":
            keyboard = {
                "inline_keyboard": [
                    [
                        {
                            "text": "Confirm",
                            "callback_data": anomaly_callback_data(
                                "confirm",
                                anomaly.id,
                            ),
                        },
                        {
                            "text": "Deny",
                            "callback_data": anomaly_callback_data(
                                "deny",
                                anomaly.id,
                            ),
                        },
                    ]
                ]
            }
        return text, keyboard

    def post_scrape_anomaly(
        self,
        anomaly: ScrapeAnomaly,
        run: ScraperRun,
    ) -> tuple[str, str]:
        text, keyboard = self._anomaly_message(anomaly, run)
        data = self._call(
            "sendMessage",
            {
                "chat_id": self.settings.telegram_chat_id,
                "text": text,
                "reply_markup": keyboard,
            },
        )
        result = data["result"]
        return str(result["chat"]["id"]), str(result["message_id"])

    def update_scrape_anomaly(
        self,
        anomaly: ScrapeAnomaly,
        run: ScraperRun,
    ) -> None:
        text, keyboard = self._anomaly_message(anomaly, run)
        self._call(
            "editMessageText",
            {
                "chat_id": anomaly.telegram_chat_id,
                "message_id": int(anomaly.telegram_message_id),
                "text": text,
                "reply_markup": keyboard,
            },
        )

    def _inventory_conflict_message(
        self,
        conflict: InventoryConflict,
    ) -> tuple[str, dict]:
        snapshot = conflict.inventory_snapshot
        text = "\n".join(
            [
                "Jawnix Inventory Conflict",
                "",
                f"Older request: {conflict.older_request_id}",
                f"Newer request: {conflict.newer_request_id}",
                f"Shared eligible leads: "
                f"{len(snapshot.get('sharedLeadIds') or []):,}",
                f"Status: {conflict.status.replace('_', ' ').title()}",
                "",
                "Confirm authorizes one allocation attempt for this exact "
                "inventory snapshot. Deny leaves the newer request waiting.",
            ]
        )
        keyboard = {"inline_keyboard": []}
        if conflict.status == "pending":
            keyboard = {
                "inline_keyboard": [
                    [
                        {
                            "text": "Confirm once",
                            "callback_data": conflict_callback_data(
                                "confirm",
                                conflict.id,
                            ),
                        },
                        {
                            "text": "Deny",
                            "callback_data": conflict_callback_data(
                                "deny",
                                conflict.id,
                            ),
                        },
                    ]
                ]
            }
        return text, keyboard

    def post_inventory_conflict(
        self,
        conflict: InventoryConflict,
    ) -> tuple[str, str]:
        text, keyboard = self._inventory_conflict_message(conflict)
        data = self._call(
            "sendMessage",
            {
                "chat_id": self.settings.telegram_chat_id,
                "text": text,
                "reply_markup": keyboard,
            },
        )
        result = data["result"]
        return str(result["chat"]["id"]), str(result["message_id"])

    def update_inventory_conflict(
        self,
        conflict: InventoryConflict,
    ) -> None:
        text, keyboard = self._inventory_conflict_message(conflict)
        self._call(
            "editMessageText",
            {
                "chat_id": conflict.telegram_chat_id,
                "message_id": int(conflict.telegram_message_id),
                "text": text,
                "reply_markup": keyboard,
            },
        )
