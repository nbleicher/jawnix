from datetime import date
from pathlib import Path
import sys
from types import SimpleNamespace

import asyncpg
import psycopg2
import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "control"))
from yield_gate import eligible_jobs, sample_cells


@pytest.mark.asyncio
async def test_pilot_gates_expansion_and_retains_history(seeded_database):
    import os
    conn = psycopg2.connect(os.environ["TEST_DATABASE_URL"])
    conn.autocommit = True
    today = date.today()
    keyword = "yield fixture"
    jobs = [{"keyword": keyword,"state":"tn","geo_coordinates":f"{35 + n / 10:.1f},-86"} for n in range(20)]
    ledger = SimpleNamespace(conn=conn)
    try:
        pilot = eligible_jobs(ledger,jobs,today,today)
        assert len(pilot)==6
        cells = [job["geo_coordinates"] for job in pilot]
        with conn.cursor() as cur:
            # Transport errors do not count as genuine empty searches or rejected yield.
            for n, cell in enumerate(cells):
                cur.execute("""INSERT INTO scrape_attempts (job_id,attempt,keyword,state,cell,outcome)
                    VALUES (%s,1,%s,'tn',%s,'blocked')""", (8000+n,keyword,cell))
        assert len(eligible_jobs(ledger,jobs,today,today))==6
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM keyword_yield_evaluations WHERE keyword=%s",(keyword,))
            assert cur.fetchone()[0]=="testing"
            for n, cell in enumerate(cells):
                cur.execute("""INSERT INTO scrape_attempts (job_id,attempt,keyword,state,cell,outcome)
                    VALUES (%s,2,%s,'tn',%s,'empty')""",(8000+n,keyword,cell))
                cur.execute("INSERT INTO scrape_results(job_id,keyword,yield_verified) VALUES(%s,%s,true)",(8000+n,keyword))
        assert eligible_jobs(ledger,jobs,today,today)==[]
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM keyword_yield_evaluations WHERE keyword=%s",(keyword,))
            assert cur.fetchone()[0]=="rejected"
            cur.execute("SELECT count(*) FROM keyword_history WHERE keyword='plumbers'")
            assert cur.fetchone()[0]>0
        # Productive candidates unlock one wave without resetting history.
        productive = [{**job,"keyword":"productive fixture"} for job in jobs]
        pilot = eligible_jobs(ledger,productive,today,today)
        with conn.cursor() as cur:
            for n, job in enumerate(pilot):
                cur.execute("""INSERT INTO scrape_attempts (job_id,attempt,keyword,state,cell,outcome,result_count,phone_count)
                    VALUES (%s,1,'productive fixture','tn',%s,'success',2,2)""",(9000+n,job["geo_coordinates"]))
                cur.execute("""INSERT INTO scrape_results(job_id,keyword,result_count,phone_count,new_phone_count,yield_verified)
                    VALUES(%s,'productive fixture',2,2,%s,true)""",(9000+n,2 if n<2 else 0))
        expanded=eligible_jobs(ledger,productive,today,today)
        assert len(expanded)==18
        with conn.cursor() as cur:
            for n,job in enumerate([job for job in expanded if job not in pilot]):
                cur.execute("""INSERT INTO scrape_attempts(job_id,attempt,keyword,state,cell,outcome)
                    VALUES(%s,1,'productive fixture','tn',%s,'success')""",(9030+n,job["geo_coordinates"]))
                cur.execute("""INSERT INTO scrape_results(job_id,keyword,result_count,phone_count,new_phone_count,yield_verified)
                    VALUES(%s,'productive fixture',20,20,0,true)""",(9030+n,))
        assert eligible_jobs(ledger,productive,today,today)==[]
        with conn.cursor() as cur:
            cur.execute("SELECT status,new_phone_count,jsonb_array_length(coverage_cells) FROM keyword_yield_evaluations WHERE keyword='productive fixture'")
            assert cur.fetchone()==('completed',4,18)
            cur.execute("SELECT count(*) FROM keyword_history WHERE keyword='productive fixture'")
            assert cur.fetchone()[0]==0
    finally:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM scrape_attempts WHERE job_id BETWEEN 8000 AND 9050")
            cur.execute("DELETE FROM scrape_results WHERE job_id BETWEEN 8000 AND 9050")
            cur.execute("DELETE FROM keyword_yield_evaluations WHERE keyword IN ('yield fixture','productive fixture')")
        conn.close()


def test_pilot_prioritizes_productive_geographically_spread_cells():
    cells = [f"{n},-86" for n in range(20)]
    scores = {cell:100 for cell in cells[10:18]}
    sampled = sample_cells(cells,scores)
    assert len(sampled)==6
    assert all(cell in scores for cell in sampled)
    assert len(set(sampled))==6


@pytest.mark.asyncio
async def test_rollover_accounts_for_rejected_pilot(app_client):
    client, settings = app_client
    conn = await asyncpg.connect(settings.database_url)
    try:
        today = date.today()
        await conn.execute("""INSERT INTO keyword_yield_evaluations
            (keyword,state,started_on,sample_cells,full_cells,status)
            VALUES ('electricians','oh',$1,'["a","b","c","d","e","f"]',100,'rejected')""",today)
        from app import queries
        result = await conn.fetchrow(queries.AUTO_KEYWORD_ROLLOVER_PROGRESS,["electricians"],["oh"],today)
        assert result["completed_pairs"]==1
        assert result["avoided_jobs"]==94
    finally:
        await conn.execute("DELETE FROM keyword_yield_evaluations WHERE keyword='electricians'")
        await conn.close()


@pytest.mark.asyncio
async def test_recovery_pause_is_visible_and_preserves_operator_pause(app_client):
    client, settings = app_client
    conn = await asyncpg.connect(settings.database_url)
    try:
        await conn.execute("UPDATE acquisition_health SET blocked_until=now()+interval '2 minutes',last_probe_outcome='blocked'")
        response = await client.get("/api/dashboard/stack")
        assert response.status_code == 200
        assert response.json()["stack_status"]["key"] == "attention"
        assert "recovery paused" in response.json()["stack_status"]["label"].lower()
        response = await client.get("/api/dashboard/activity")
        assert response.json()["pipeline_state"]["label"] == "Recovery paused"
        settings.pipeline_pause_path.parent.mkdir(parents=True,exist_ok=True)
        settings.pipeline_pause_path.write_text('{"mode":"drain"}')
        await conn.execute("UPDATE acquisition_health SET blocked_until=NULL,last_probe_outcome='success'")
        response = await client.get("/api/dashboard/activity")
        assert response.json()["pipeline_state"]["key"] in {"pausing","paused"}
        assert settings.pipeline_pause_path.exists()
    finally:
        await conn.execute("UPDATE acquisition_health SET blocked_until=NULL,last_probe_outcome=NULL")
        await conn.close()
