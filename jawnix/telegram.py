from __future__ import annotations

import hmac
import logging
import re
import uuid

import httpx

from .config import Settings
from .fulfillment import RequestActionContext, telegram_actions
from .models import (
    InventoryConflict,
    LeadRequest,
    NightlyReview,
    ScrapeAnomaly,
    ScraperRun,
    SourceRecommendation,
)


logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

ACTION_PREFIX = "jawnix"
ANOMALY_PREFIX = "jawnix-a"
CONFLICT_PREFIX = "jawnix-c"
RECOMMENDATION_PREFIX = "jawnix-r"
ALLOWED_ACTIONS = {"approve", "reject", "retry", "retry_delivery"}
ALLOWED_ANOMALY_ACTIONS = {"confirm", "deny"}
ALLOWED_CONFLICT_ACTIONS = {"confirm", "deny"}
ALLOWED_RECOMMENDATION_ACTIONS = {"approve", "deny"}


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


def recommendation_callback_data(
    action: str,
    recommendation_id: uuid.UUID,
    *,
    evidence_checksum: str = "",
    configuration_version: int | None = None,
) -> str:
    if action not in ALLOWED_RECOMMENDATION_ACTIONS:
        raise ValueError(
            f"Unsupported Source Recommendation action: {action}"
        )
    short_action = {"approve": "a", "deny": "d"}[action]
    version = (
        format(configuration_version, "x")
        if configuration_version is not None
        else "-"
    )
    checksum = evidence_checksum[:8] or "-"
    value = (
        f"{RECOMMENDATION_PREFIX}:{short_action}:"
        f"{recommendation_id.hex}:{version}:{checksum}"
    )
    if len(value.encode("utf-8")) > 64:
        raise ValueError("Telegram callback data exceeds 64 bytes.")
    return value


def parse_recommendation_callback_data(
    value: str,
) -> tuple[str, uuid.UUID, int | None, str]:
    try:
        prefix, short_action, raw_id, raw_version, checksum = (
            value.split(":", 4)
        )
        action = {"a": "approve", "d": "deny"}[short_action]
        recommendation_id = uuid.UUID(raw_id)
        configuration_version = (
            int(raw_version, 16) if raw_version != "-" else None
        )
        if checksum != "-" and not re.fullmatch(
            r"[0-9a-f]{8}",
            checksum,
        ):
            raise ValueError
    except (KeyError, ValueError, AttributeError):
        raise ValueError(
            "Malformed Telegram recommendation callback data."
        ) from None
    if (
        prefix != RECOMMENDATION_PREFIX
        or action not in ALLOWED_RECOMMENDATION_ACTIONS
    ):
        raise ValueError(
            "Unsupported Telegram recommendation callback data."
        )
    return (
        action,
        recommendation_id,
        configuration_version,
        "" if checksum == "-" else checksum,
    )


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
    """Offer exactly what the Fulfillment workspace offers.

    Telegram used to carry its own copy of the state rules, which is how a
    channel drifts into presenting an action the domain refuses. Both surfaces
    now read jawnix/fulfillment.py, so an administrator sees one decision
    rather than two that can disagree (#57).
    """
    context = RequestActionContext(
        status=request.status,
        has_artifact=request.artifact is not None,
    )
    buttons = [
        {
            "text": action.label,
            "callback_data": callback_data(action.name, request.id),
        }
        for action in telegram_actions(context)
    ]
    return {"inline_keyboard": [buttons] if buttons else []}


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

    def post_action_failure(self, text: str) -> str:
        """Tell the operator an asynchronously queued action did not run.

        The webhook answers the callback immediately so Telegram stops showing
        a spinner, but the durable command runs later in a worker. A real
        command failure therefore needs its own message; otherwise Telegram
        says only "Queued" while Jawnix records a failed job.

        ``text`` is deliberately normalized by the worker rather than built
        from the raw exception, so private paths and backend details never
        cross the integration boundary.
        """
        if not self.settings.telegram_chat_id:
            raise RuntimeError("TELEGRAM_CHAT_ID is not configured.")
        data = self._call(
            "sendMessage",
            {
                "chat_id": self.settings.telegram_chat_id,
                "text": text[:4000],
            },
        )
        return str(data["result"]["message_id"])

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
                f"Review: {self.settings.public_base_url}/app/admin/acquisition#nightly-reviews",
            ]
        )
        keyboard_rows: list[list[dict[str, str]]] = []
        if anomaly is not None and anomaly.status == "pending":
            keyboard_rows.append(
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
            )
        for item in recommendations:
            if item.get("status") != "pending":
                continue
            recommendation_id = uuid.UUID(str(item["id"]))
            label = (
                f"{str(item.get('action') or '').title()} "
                f"{str(item.get('segment') or '')[:24]}"
            )
            keyboard_rows.append(
                [
                    {
                        "text": f"Approve {label}",
                        "callback_data": recommendation_callback_data(
                            "approve",
                            recommendation_id,
                            evidence_checksum=str(
                                item.get("evidenceChecksum") or ""
                            ),
                            configuration_version=(
                                int(item["configurationVersion"])
                                if item.get("configurationVersion")
                                is not None
                                else None
                            ),
                        ),
                    },
                    {
                        "text": "Deny",
                        "callback_data": recommendation_callback_data(
                            "deny",
                            recommendation_id,
                            evidence_checksum=str(
                                item.get("evidenceChecksum") or ""
                            ),
                            configuration_version=(
                                int(item["configurationVersion"])
                                if item.get("configurationVersion")
                                is not None
                                else None
                            ),
                        ),
                    },
                ]
            )
        keyboard = {"inline_keyboard": keyboard_rows}
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
