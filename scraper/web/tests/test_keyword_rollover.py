import importlib.util
import os
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import psycopg2
import pytest


def load_rollover():
    path = Path(__file__).parents[2] / "control" / "keyword_rollover.py"
    spec = importlib.util.spec_from_file_location("gms_test_keyword_rollover", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_write_keywords_is_atomic_and_backed_up(tmp_path):
    rollover = load_rollover()
    path = tmp_path / "keywords.txt"
    path.write_text("Old Service\n")
    rollover.write_keywords(path, ["New Service", "Second Service"])
    assert path.read_text() == "New Service\nSecond Service\n"
    assert list(tmp_path.glob("keywords.txt.bak.*"))


class RecordingCursor:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, statement, parameters=None):
        pass


class CommitFailureConnection:
    def __init__(self):
        self.rolled_back = False

    def cursor(self):
        return RecordingCursor()

    def commit(self):
        raise RuntimeError("database commit failed")

    def rollback(self):
        self.rolled_back = True


def test_activation_restores_keyword_file_when_database_commit_fails(tmp_path):
    rollover = load_rollover()
    path = tmp_path / "keywords.txt"
    path.write_text("Old Service\n", encoding="utf-8")
    connection = CommitFailureConnection()

    with pytest.raises(RuntimeError, match="database commit failed"):
        rollover.activate_generation(
            connection=connection,
            keywords_path=path,
            current=["Old Service"],
            result=SimpleNamespace(
                keywords=["New Service"],
                excluded_count=3,
            ),
            generation_id=uuid4(),
            model="test-model",
        )

    assert connection.rolled_back
    assert path.read_text(encoding="utf-8") == "Old Service\n"


def test_campaign_complete_waits_for_active_jobs():
    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is required")
    rollover = load_rollover()
    connection = psycopg2.connect(dsn)
    connection.autocommit = True
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO keyword_history (keyword,state,last_enqueued)
                   VALUES ('electricians','oh',CURRENT_DATE)
                   ON CONFLICT (keyword,state) DO UPDATE SET last_enqueued=CURRENT_DATE"""
            )
        assert not rollover.campaign_complete(
            connection, ["electricians"], ["oh"], date.today(),
        )
        assert not rollover.campaign_complete(
            connection, ["electricians"], ["oh", "ky"], date.today(),
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE river_job SET state='completed', finalized_at=NOW() WHERE id=102"
            )
        assert rollover.campaign_complete(
            connection, ["electricians"], ["oh"], date.today(),
        )
    finally:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE river_job SET state='running', finalized_at=NULL WHERE id=102"
            )
        connection.close()
