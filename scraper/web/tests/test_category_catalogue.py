import os
import sys
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import psycopg2
import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "control"))
from category_catalogue import category_identity, prepare, sync_catalogue
from yield_gate import eligible_jobs


def test_alias_identity_preserves_distinct_business_types():
    assert category_identity("roofing")[0] == category_identity("roofing contractor")[0]
    assert (
        category_identity("car audio installation company")[0]
        == category_identity("car audio installer")[0]
    )
    assert (
        category_identity("car audio installer")[0]
        == category_identity("Car Audio System Installer")[0]
    )
    assert (
        category_identity("car audio store")[0]
        != category_identity("car audio installer")[0]
    )
    assert (
        category_identity("rope access contractor")[0]
        != category_identity("ropes course")[0]
    )


@pytest.mark.asyncio
async def test_catalogue_reuses_history_in_an_untried_state_and_preserves_coverage(
    seeded_database,
):
    conn = psycopg2.connect(os.environ["TEST_DATABASE_URL"])
    conn.autocommit = True
    ledger = SimpleNamespace(conn=conn)
    today = date.today()
    term = "catalogue untried fixture"
    cfg = {"states": ["tn", "sc"], "queue": {}}

    def build(config, terms):
        return [
            {"keyword": term, "state": state, "geo_coordinates": f"{35 + i / 100},-86"}
            for term in terms
            for state in config["states"]
            for i in range(20)
        ]

    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO keyword_history VALUES(%s,'tn',%s)",
                (term, today - timedelta(days=1)),
            )
        sync_catalogue(ledger, [term], today, origin="historical_campaign")
        jobs, _, _ = prepare(ledger, cfg, today, build, eligible_jobs)
        assert (
            len(
                [job for job in jobs if job["keyword"] == term and job["state"] == "sc"]
            )
            == 6
        )
        assert not any(job["keyword"] == term and job["state"] == "tn" for job in jobs)
        category_id = category_identity(term)[0]
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO acquisition_category_cells(category_id,state,cell) VALUES(%s,'sc','35.0,-86')",
                (category_id,),
            )
        # The initial grid stays fixed after a cell is posted and across restart.
        again, _, _ = prepare(
            ledger, cfg, today + timedelta(days=1), build, eligible_jobs
        )
        assert len([job for job in again if job["keyword"] == term]) == 5
        with conn.cursor() as cur:
            cur.execute(
                "SELECT jsonb_array_length(cells),started_on FROM acquisition_category_states WHERE category_id=%s AND state='sc'",
                (category_id,),
            )
            assert cur.fetchone() == (20, today)
    finally:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM acquisition_categories WHERE canonical_query=%s", (term,)
            )
            cur.execute("DELETE FROM keyword_history WHERE keyword=%s", (term,))
            cur.execute(
                "DELETE FROM keyword_yield_evaluations WHERE keyword=%s", (term,)
            )
        conn.close()


@pytest.mark.asyncio
async def test_catalogue_retains_rejected_alias_decisions(seeded_database):
    conn = psycopg2.connect(os.environ["TEST_DATABASE_URL"])
    conn.autocommit = True
    ledger = SimpleNamespace(conn=conn)
    today = date.today()
    try:
        sync_catalogue(
            ledger, ["car audio system installer"], today, origin="approved_campaign"
        )
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO keyword_yield_evaluations(keyword,state,started_on,sample_cells,full_cells,status) VALUES('car audio system installer','sc',%s,'[]',20,'rejected')",
                (today - timedelta(days=1),),
            )

        def build(config, terms):
            return [
                {
                    "keyword": term,
                    "state": "sc",
                    "geo_coordinates": f"{35 + i / 100},-86",
                }
                for term in terms
                for i in range(20)
            ]

        jobs, _, _ = prepare(
            ledger, {"states": ["sc"], "queue": {}}, today, build, eligible_jobs
        )
        assert not any(job["keyword"] == "car audio installation" for job in jobs)
    finally:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM acquisition_categories WHERE canonical_query='car audio installation'"
            )
            cur.execute(
                "DELETE FROM keyword_yield_evaluations WHERE keyword='car audio system installer'"
            )
        conn.close()


@pytest.mark.asyncio
async def test_catalogue_workspace_reports_catalogue_progress(app_client):
    import asyncpg

    client, settings = app_client
    settings.category_catalogue_enabled = True
    settings.active_states_path.write_text(
        "states: [tn, sc]\nsettings: {}\nqueue: {}\noverrides: {}"
    )
    conn = psycopg2.connect(settings.database_url)
    conn.autocommit = True
    ledger = SimpleNamespace(conn=conn)
    term = "catalogue status fixture"
    async_conn = await asyncpg.connect(settings.database_url)
    try:
        sync_catalogue(ledger, [term], date.today(), origin="approved_campaign")
        await async_conn.execute(
            "INSERT INTO keyword_history VALUES($1,'tn',current_date)", term
        )
        await async_conn.execute(
            "INSERT INTO app_config(key,value,encrypted) VALUES('auto_keyword_rollover','true',false) ON CONFLICT(key) DO UPDATE SET value='true'"
        )
        response = await client.get("/api/keywords")
        assert response.status_code == 200
        rollover = response.json()["rollover"]
        assert rollover["label"] == "Category catalogue active"
        assert rollover["state"] == "working"
        assert rollover["percent_complete"] == 50
        assert rollover["posted_jobs"] is None and rollover["expected_jobs"] is None
        assert "1 of 2 state checks complete" in rollover["detail"]
    finally:
        await async_conn.execute("DELETE FROM keyword_history WHERE keyword=$1", term)
        await async_conn.execute(
            "DELETE FROM acquisition_categories WHERE canonical_query=$1", term
        )
        await async_conn.close()
        conn.close()


@pytest.mark.asyncio
async def test_catalogue_does_not_treat_a_partial_old_post_as_verified_yield(
    seeded_database,
):
    conn = psycopg2.connect(os.environ["TEST_DATABASE_URL"])
    conn.autocommit = True
    ledger = SimpleNamespace(conn=conn)
    today = date.today()
    term = "catalogue partial legacy fixture"

    def build(config, terms):
        return [
            {"keyword": word, "state": "sc", "geo_coordinates": f"{35 + i / 100},-86"}
            for word in terms
            for i in range(20)
        ]

    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO enqueue_log(keyword,state,cell,day,status) VALUES(%s,'sc','35.0,-86',%s,'posted')",
                (term, today),
            )
        sync_catalogue(ledger, [term], today, origin="historical_campaign")
        jobs, _, _ = prepare(
            ledger, {"states": ["sc"], "queue": {}}, today, build, eligible_jobs
        )
        assert len([job for job in jobs if job["keyword"] == term]) == 6
        assert all(
            job["geo_coordinates"] != "35.0,-86"
            for job in jobs
            if job["keyword"] == term
        )
    finally:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM acquisition_categories WHERE canonical_query=%s", (term,)
            )
            cur.execute("DELETE FROM enqueue_log WHERE keyword=%s", (term,))
            cur.execute(
                "DELETE FROM keyword_yield_evaluations WHERE keyword=%s", (term,)
            )
        conn.close()
