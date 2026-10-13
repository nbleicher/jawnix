from pathlib import Path
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import psycopg2
import pytest

sys.path.insert(0,str(Path(__file__).parents[2]/"worker"))
from phone_yield import claim_new_phones, membership, LookupUnavailable


def test_empty_results_require_no_lookup():
    assert membership([])==(set(),set())


def test_missing_lookup_fails_closed_without_credentials_in_error():
    row=[None]*18;row[3]='Fixture';row[4]='6155550101';row[13]='tn'
    with pytest.raises(LookupUnavailable):
        membership([row],lookup_url='',token='')


@pytest.mark.asyncio
async def test_endpoint_credit_is_global_replayable_and_atomic(seeded_database):
    dsn=os.environ['TEST_DATABASE_URL']
    phones={'6155550190','6155550191','6155550192'}
    existing={'6155550190'}
    barrier=threading.Barrier(2)
    def discover(job):
        conn=psycopg2.connect(dsn)
        try:
            barrier.wait()
            with conn.cursor() as cur:
                count=claim_new_phones(cur,job,phones,existing)
            conn.commit()
            return count
        finally:conn.close()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            counts=list(pool.map(discover,[11001,11002]))
        assert sum(counts)==2
        conn=psycopg2.connect(dsn)
        with conn,conn.cursor() as cur:
            assert claim_new_phones(cur,11001,phones,phones)==counts[0]
            assert claim_new_phones(cur,11002,phones,phones)==counts[1]
            cur.execute('SELECT count(*) FROM acquisition_phone_registry WHERE phone=%s',('6155550190',))
            assert cur.fetchone()[0]==0
            claim_new_phones(cur,11003,{'6155550193'},set())
            conn.rollback()
            cur.execute('SELECT count(*) FROM acquisition_phone_registry WHERE phone=%s',('6155550193',))
            assert cur.fetchone()[0]==0
        conn.close()
    finally:
        conn=psycopg2.connect(dsn)
        with conn,conn.cursor() as cur:cur.execute('DELETE FROM acquisition_phone_registry WHERE first_job_id BETWEEN 11001 AND 11003')
        conn.close()
