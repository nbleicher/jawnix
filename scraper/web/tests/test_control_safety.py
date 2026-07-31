import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


def load_module(name: str, relative_path: str):
    path = Path(__file__).parents[2] / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeChannel:
    def recv_exit_status(self):
        return 0


class FakeStream:
    channel = FakeChannel()

    def __init__(self, value: bytes = b""):
        self.value = value

    def read(self):
        return self.value


class FakeSsh:
    def exec_command(self, command):
        assert command == "printf '%s' \"$HOME\""
        return None, FakeStream(b"/home/scraper"), FakeStream()


def test_remote_keyword_path_expands_before_sftp():
    push = load_module("gms_test_push_keywords", "control/push_keywords.py")

    assert push.resolve_remote_path(
        FakeSsh(), "~/scraper/keywords.txt",
    ) == "/home/scraper/scraper/keywords.txt"
    assert push.resolve_remote_path(
        FakeSsh(), "/srv/scraper/keywords.txt",
    ) == "/srv/scraper/keywords.txt"


@pytest.mark.parametrize(
    ("health", "queue_depth", "expected"),
    [
        ({"status": "ok", "active_jobs": 1, "results_per_minute": 2}, 5, False),
        ({"status": "ok", "active_jobs": 0, "results_per_minute": 2}, 5, True),
        ({"status": "ok", "active_jobs": 1, "results_per_minute": 0}, 5, True),
        ({"status": "ok", "active_jobs": 0, "results_per_minute": 0}, 0, False),
        ({"status": "unreachable", "active_jobs": 1, "results_per_minute": 2}, 5, True),
    ],
)
def test_worker_health_includes_queue_starvation(
    health,
    queue_depth,
    expected,
):
    heartbeat = load_module("gms_test_heartbeat", "worker/heartbeat.py")

    started_at = (
        datetime.now(timezone.utc) - timedelta(minutes=10)
    ).isoformat()
    assert heartbeat.worker_is_unhealthy(
        health,
        queue_depth,
        started_at=started_at,
    ) is expected


def test_zero_throughput_waits_for_grace_period():
    heartbeat = load_module("gms_test_heartbeat_grace", "worker/heartbeat.py")
    started_at = datetime.now(timezone.utc).isoformat()

    assert not heartbeat.worker_is_unhealthy(
        {"status": "ok", "active_jobs": 1, "results_per_minute": 0},
        queue_depth=5,
        started_at=started_at,
        zero_throughput_grace_seconds=300,
    )
