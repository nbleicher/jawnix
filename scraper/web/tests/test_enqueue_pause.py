from datetime import date
from pathlib import Path
import importlib.util
import sys

import pytest


CONTROL = Path(__file__).parents[2] / "control"
sys.path.insert(0, str(CONTROL))
spec = importlib.util.spec_from_file_location("enqueue_pause_test", CONTROL / "enqueue.py")
enqueue = importlib.util.module_from_spec(spec)
spec.loader.exec_module(enqueue)


class Ledger:
    def queue_depth(self):
        return 0

    def close(self):
        pass


def test_top_up_stops_before_insert_when_paused(tmp_path, monkeypatch):
    pause_file = tmp_path / "pipeline.paused"
    pause_file.touch()

    def unexpected_insert(*args, **kwargs):
        raise AssertionError("paused pipeline attempted to insert a job")

    monkeypatch.setattr(enqueue, "insert_job", unexpected_insert)
    cursor, inserted, depth = enqueue.top_up(
        "http://queue.test",
        [{"keyword": "plumbers", "state": "oh", "geo_coordinates": "1,2"}],
        0, 10, 10, Ledger(), date.today(), {}, pause_file=pause_file,
    )

    assert (cursor, inserted, depth) == (0, 0, 0)


def test_watch_stays_alive_when_every_job_is_already_covered(
    tmp_path, monkeypatch,
):
    class WatchObserved(RuntimeError):
        pass

    pause_file = tmp_path / "pipeline.paused"
    pause_file.touch()
    prepared = (
        {}, [], {}, Ledger(), date.today(), "http://queue.test", 10, 10, 1, "",
    )
    monkeypatch.setattr(enqueue, "prepare_jobs", lambda args: prepared)
    monkeypatch.setattr(
        enqueue.time,
        "sleep",
        lambda seconds: (_ for _ in ()).throw(WatchObserved),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["enqueue.py", "--watch", "--pause-file", str(pause_file)],
    )

    with pytest.raises(WatchObserved):
        enqueue.main()
