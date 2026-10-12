import importlib.util
from pathlib import Path


SCALE = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


heartbeat = load("heartbeat", SCALE / "worker" / "heartbeat.py")
uptime = load("uptime_probe", SCALE / "control" / "uptime_probe.py")


def test_worker_collection_excludes_the_application_worker(monkeypatch):
    class Result:
        returncode = 0
        stdout = ""

    def docker_ps(command, **kwargs):
        result = Result()
        result.stdout = "gms-scale-worker-1\n"
        if "label=com.docker.compose.project=gms-scale" not in command:
            result.stdout += "jawnix-worker-1\n"
        return result

    monkeypatch.setattr(heartbeat, "run", docker_ps)
    assert heartbeat.docker_service_containers("worker") == ["gms-scale-worker-1"]


def test_memory_and_spool_metrics(tmp_path):
    memory = tmp_path / "meminfo"
    memory.write_text("MemTotal:       1000 kB\nMemAvailable:    250 kB\n")
    assert heartbeat.memory_bytes(memory) == (750 * 1024, 1000 * 1024)
    assert heartbeat.spool_metrics(tmp_path) == (0, 0)
    (tmp_path / "result.ndjson.done").write_text("done")
    assert heartbeat.spool_metrics(tmp_path)[0] == 1


def test_systemd_states(monkeypatch):
    class Result:
        returncode = 0
        stdout = "LoadState=loaded\nActiveState=active\nSubState=running\n"
    monkeypatch.setattr(heartbeat, "run", lambda *args, **kwargs: Result())
    states = heartbeat.systemd_states()
    assert states["docker.service"]["active"] == "active"
    assert "gms-dataset-publication.timer" in states
    assert "gms-database-cache.timer" in states
    assert "gms-keyword-rollover.timer" not in states
    assert states["external_heartbeat"]["active"] in {"active", "disabled"}


def test_uptime_probe_success_and_failure(monkeypatch):
    sent = []
    monkeypatch.setattr(uptime, "HEARTBEAT_URL", "https://example.test/heartbeat/token")
    monkeypatch.setattr(
        uptime,
        "CHECKS",
        (("scraper control", "http://local/healthz", "token", True),),
    )
    monkeypatch.setattr(uptime, "request_ok", lambda *args: (True, "ok"))
    monkeypatch.setattr(uptime, "send_heartbeat", lambda url, failures: sent.append(failures) or True)
    assert uptime.main() == 0
    assert sent == [[]]
    monkeypatch.setattr(uptime, "request_ok", lambda *args: (False, "connection refused"))
    assert uptime.main() == 1
    assert sent[-1] == ["scraper control: connection refused"]
