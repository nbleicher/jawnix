from pathlib import Path
import json
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "control"))
import alert


@pytest.fixture
def pipeline(monkeypatch, tmp_path):
    """Run alert.main against a fake pipeline; returns (state, run, sent)."""
    state = {"queued": 0, "running": 0, "finished": 750, "deliver": True}
    sent = []

    monkeypatch.setattr(alert, "api_pending", lambda *a: (0, None))
    monkeypatch.setattr(
        alert, "db_metrics",
        lambda dsn, window: (100, 0.1, state["queued"], state["running"], 0, 0.0, state["finished"], None),
    )
    monkeypatch.setattr(alert, "acquisition_alerts", lambda dsn: [])
    monkeypatch.setattr(alert, "record_event", lambda *a: None)
    monkeypatch.setattr(alert, "spool_metrics", lambda d: (None, None))

    def send(url, text):
        sent.append(text)
        return state["deliver"]

    monkeypatch.setattr(alert, "send_webhook", send)
    state_file = tmp_path / "runtime" / "alert-state.json"

    def run():
        monkeypatch.setattr(sys, "argv", [
            "alert.py", "--dsn", "postgresql://test", "--min-depth", "1",
            "--webhook", "https://hooks.test/x", "--state-file", str(state_file),
        ])
        return alert.main()

    return state, run, sent


def test_empty_queue_between_top_ups_is_not_starved(pipeline):
    state, run, sent = pipeline
    state.update(queued=0, running=0, finished=750)
    assert run() == 0
    assert sent == []


def test_empty_queue_with_no_finished_jobs_is_starved(pipeline):
    state, run, sent = pipeline
    state.update(queued=0, running=0, finished=0)
    assert run() == 1
    assert len(sent) == 1 and "queue starved" in sent[0]


def test_persisting_alert_is_sent_once_then_recovery_once(pipeline):
    state, run, sent = pipeline
    state.update(finished=0)
    run()
    run()
    run()
    assert len(sent) == 1
    state.update(finished=750)
    run()
    run()
    assert len(sent) == 2 and sent[1].startswith("✅ Scraper recovered")


def test_failed_delivery_is_retried_next_run(pipeline):
    state, run, sent = pipeline
    state.update(finished=0, deliver=False)
    run()
    state.update(deliver=True)
    run()
    run()
    assert len(sent) == 2


def test_unreadable_state_file_forces_a_resend(pipeline, tmp_path):
    state, run, sent = pipeline
    state.update(finished=0)
    run()
    (tmp_path / "runtime" / "alert-state.json").write_text("not json")
    run()
    assert len(sent) == 2
    assert json.loads((tmp_path / "runtime" / "alert-state.json").read_text())["keys"]


def test_alert_key_ignores_changing_counts():
    assert alert.alert_key("queue backing up: 600 queued > max 500") == alert.alert_key(
        "queue backing up: 900 queued > max 500")
    assert alert.alert_key("oldest queued job is 45m old > 30m") == alert.alert_key(
        "oldest queued job is 61m old > 30m")
    assert alert.alert_key("queue starved: x") != alert.alert_key("retry storm: x")
