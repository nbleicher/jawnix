import asyncio
import os

import asyncpg
import pytest

from app import queries


@pytest.mark.asyncio
async def test_totals_read_does_not_wait_for_acquisition_table_lock(app_client):
    client, _ = app_client
    connection = await asyncpg.connect(os.environ["TEST_DATABASE_URL"])
    try:
        async with connection.transaction():
            await connection.execute("LOCK TABLE businesses IN ACCESS EXCLUSIVE MODE")
            response = await asyncio.wait_for(client.get("/api/dashboard/stats"), 0.5)
            assert response.status_code == 200
            assert response.json()["stats"]["businesses"] == 5
            assert response.json()["stats_fetched_at"]
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_recent_activity_does_not_scan_historical_results(app_client):
    connection = await asyncpg.connect(os.environ["TEST_DATABASE_URL"])
    try:
        async with connection.transaction():
            await connection.execute("""
                INSERT INTO scrape_results (job_id,keyword,result_count,phone_count,created_at)
                SELECT 9000000+i,'historical',0,0,now()-interval '12 hours'
                FROM generate_series(1,50000) i
            """)
            await connection.execute("ANALYZE scrape_results")
            import json
            plan = json.loads(await connection.fetchval(
                "EXPLAIN (ANALYZE,FORMAT JSON) " + queries.PIPELINE_ACTIVITY
            ))[0]["Plan"]

            def scanned_results(node):
                own = (
                    node.get("Actual Rows", 0) * node.get("Actual Loops", 1)
                    if node.get("Relation Name") == "scrape_results" else 0
                )
                return max([own, *(scanned_results(child) for child in node.get("Plans", []))])

            assert scanned_results(plan) <= 100
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_idle_activity_preserves_latest_write_and_all_time_totals(app_client):
    connection = await asyncpg.connect(os.environ["TEST_DATABASE_URL"])
    try:
        async with connection.transaction():
            await connection.execute("UPDATE scrape_results SET created_at=now()-interval '12 hours'")
            await connection.execute("UPDATE businesses SET last_seen=now()-interval '12 hours'")
            row = await connection.fetchrow(queries.PIPELINE_ACTIVITY)
            assert row["jobs_last_minute"] == row["jobs_last_five_minutes"] == 0
            assert row["businesses_last_minute"] == row["businesses_last_five_minutes"] == 0
            assert row["businesses_total"] == 5
            assert row["latest_result_at"] is not None
            assert row["latest_business_at"] is not None
            assert row["latest_write_at"] is not None
    finally:
        await connection.close()
