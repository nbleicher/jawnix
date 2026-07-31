#!/usr/bin/env python3
"""Report local stack availability to an outbound Better Stack heartbeat."""

from __future__ import annotations

import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


HEARTBEAT_URL = os.environ.get("UPTIME_HEARTBEAT_URL", "").strip()
API_KEY = os.environ.get("API_KEY", "")
CHECKS = (
    ("dashboard/database", os.environ.get("DASHBOARD_HEALTH_URL", "http://127.0.0.1:8090/healthz"), None),
    ("queue API", os.environ.get("QUEUE_API_URL", "http://127.0.0.1:8080/api/v1/jobs"), API_KEY),
)


def request_ok(url: str, api_key: str | None = None) -> tuple[bool, str]:
    request = Request(url)
    if api_key:
        request.add_header("X-API-Key", api_key)
    try:
        with urlopen(request, timeout=10) as response:
            if 200 <= response.status < 300:
                return True, "ok"
            return False, f"HTTP {response.status}"
    except HTTPError as error:
        return False, f"HTTP {error.code}"
    except (URLError, TimeoutError, OSError) as error:
        return False, str(error.reason if isinstance(error, URLError) else error)


def send_heartbeat(url: str, failures: list[str]) -> bool:
    target = url.rstrip("/") + ("/fail" if failures else "")
    body = "\n".join(failures).encode() if failures else None
    request = Request(target, data=body, method="POST" if body else "GET")
    if body:
        request.add_header("Content-Type", "text/plain; charset=utf-8")
    try:
        with urlopen(request, timeout=10) as response:
            return 200 <= response.status < 300
    except Exception as error:
        print(f"heartbeat delivery failed: {error}", file=sys.stderr)
        return False


def main() -> int:
    if not HEARTBEAT_URL:
        print("uptime heartbeat disabled: UPTIME_HEARTBEAT_URL is not set")
        return 0
    failures = []
    for name, url, api_key in CHECKS:
        healthy, detail = request_ok(url, api_key)
        if not healthy:
            failures.append(f"{name}: {detail}")
    delivered = send_heartbeat(HEARTBEAT_URL, failures)
    if failures:
        print("uptime probe failed: " + "; ".join(failures), file=sys.stderr)
        return 1
    if not delivered:
        return 1
    print("uptime probe: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
