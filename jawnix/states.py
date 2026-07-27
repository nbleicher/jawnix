from __future__ import annotations

import re


US_STATES = frozenset(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS "
    "MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split()
)

_NON_DIGIT = re.compile(r"\D+")


def normalize_phone(value: object) -> str | None:
    digits = _NON_DIGIT.sub("", str(value or ""))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else None


def normalize_states(values: list[str] | tuple[str, ...] | set[str]) -> list[str]:
    normalized = sorted(
        {
            str(value).strip().upper()
            for value in values
            if str(value).strip()
        }
    )
    invalid = [state for state in normalized if state not in US_STATES]
    if invalid:
        raise ValueError(
            f"Unsupported state code(s): {', '.join(invalid)}"
        )
    return normalized


def truncate_utf8(value: str, max_bytes: int = 180) -> str:
    encoded = (
        str(value or "")
        .replace("\r", " ")
        .replace("\n", " ")
        .encode("utf-8")
    )
    if len(encoded) <= max_bytes:
        return encoded.decode("utf-8")
    return encoded[:max_bytes].decode("utf-8", errors="ignore")
