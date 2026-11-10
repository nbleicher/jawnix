"""Fulfillment Rotation on real PostgreSQL.

The Batch page's place in rotation must be the turn ``fulfill_round_robin``
takes, with timestamptz values and row locks as production has them.
"""

import pytest

from jawnix.config import Settings

from test_postgres_operator_reads import DATABASE_URL, factory  # noqa: F401
from test_rotation import assert_batch_page_matches_the_worker

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="requires disposable PostgreSQL")


def test_batch_page_place_is_the_workers_turn_on_postgres(factory, tmp_path, monkeypatch):  # noqa: F811
    settings = Settings(_env_file=None, JAWNIX_BATCH_DIR=tmp_path / "batches")
    with factory() as db:
        assert_batch_page_matches_the_worker(db, settings, monkeypatch)
