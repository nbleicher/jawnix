"""Where Stripe Checkout sends a Customer back to after a Credit Purchase.

A Customer can open Buy credits from any portal page, including halfway through
a Batch Request. Checkout returns them to that page with the outcome, and the
request form gets back the quantity, states, and file choice it had.

The path arrives from the browser, so it is rebuilt from an allow-list rather
than echoed: one of a fixed set of portal pages, only the query keys that page
understands with validated values, and always the configured public origin.
Anything else returns the Customer to Account, so this can never become an open
redirect.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from urllib.parse import parse_qsl, urlencode, urlsplit

from .states import US_STATES

DEFAULT_RETURN_PATH = "/app/account"
MAX_RETURN_PATH_LENGTH = 500

_WHOLE_NUMBER = re.compile(r"[0-9]{1,6}")
_UNSAFE = re.compile(r"[\\\x00-\x1f\x7f]")


def _request_id(value: str) -> str | None:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None


def _bounded(maximum: int) -> Callable[[str], str | None]:
    def check(value: str) -> str | None:
        if not _WHOLE_NUMBER.fullmatch(value):
            return None
        number = int(value)
        return str(number) if 1 <= number <= maximum else None

    return check


def _states(value: str) -> str | None:
    codes = [code.strip().upper() for code in value.split(",") if code.strip()]
    if not codes or any(code not in US_STATES for code in codes):
        return None
    return ",".join(sorted(set(codes)))


# The unfinished request form: quantity, states, rows per file.
_REQUEST_FORM: dict[str, Callable[[str], str | None]] = {
    "quantity": _bounded(100_000),
    "states": _states,
    "rows_per_file": _bounded(100_000),
}

# The portal pages a purchase can return to, and the query each one reads.
_RETURN_PAGES: dict[str, dict[str, Callable[[str], str | None]]] = {
    "/app/home": {},
    # Purchases started before Home replaced Overview; the page redirects.
    "/app/overview": {},
    "/app/account": {},
    # Account's tabs; Billing is where Buy credits on Account starts.
    **{
        f"/app/account/{tab}": {}
        for tab in ("states", "billing", "exclusion-lists", "password")
    },
    "/app/new-request": _REQUEST_FORM,
    "/app/batches": {"page": _bounded(100_000)},
    # Purchases started before New request and Batches had their own pages,
    # and before Feedback left; both pages redirect.
    "/app/feedback": {"request": _request_id},
    "/app/requests": {
        "request": _request_id,
        "page": _bounded(100_000),
        **_REQUEST_FORM,
    },
}

# One batch's page, named by its number or by the UUID older links carry.
_BATCH_PAGE = re.compile(
    r"/app/batches/(?:[0-9]{1,9}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)


def customer_return_path(value: str | None) -> str:
    """The validated portal path to return to, or Account when it is not one."""

    if (
        not value
        or len(value) > MAX_RETURN_PATH_LENGTH
        or not value.startswith("/")
        or value.startswith("//")
        or _UNSAFE.search(value)
    ):
        return DEFAULT_RETURN_PATH
    parts = urlsplit(value)
    if parts.scheme or parts.netloc:
        return DEFAULT_RETURN_PATH
    if _BATCH_PAGE.fullmatch(parts.path):
        return parts.path
    allowed = _RETURN_PAGES.get(parts.path)
    if allowed is None:
        return DEFAULT_RETURN_PATH
    query: dict[str, str] = {}
    for key, raw in parse_qsl(parts.query):
        check = allowed.get(key)
        if check is None or key in query:
            continue
        cleaned = check(raw)
        if cleaned is not None:
            query[key] = cleaned
    return parts.path + (f"?{urlencode(query, safe=',')}" if query else "")


def checkout_return_urls(public_base_url: str, return_to: str | None) -> tuple[str, str]:
    """Stripe's success and cancel URLs for a purchase started at ``return_to``."""

    path = customer_return_path(return_to)
    base = f"{public_base_url.rstrip('/')}{path}{'&' if '?' in path else '?'}"
    return f"{base}purchase=success", f"{base}purchase=cancelled"
