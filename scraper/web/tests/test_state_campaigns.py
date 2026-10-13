from datetime import date,timedelta
from pathlib import Path
from types import SimpleNamespace
import os,sys
import asyncpg,psycopg2,pytest
sys.path.insert(0,str(Path(__file__).parents[2]/'control'))
from state_campaigns import register,pending,order_states,prepare
from enqueue import build_jobs
import enqueue
from yield_gate import eligible_jobs

def test_independent_mode_cannot_fall_back_to_unguarded_enqueue(monkeypatch):
    monkeypatch.setenv('GMS_STATE_BACKLOGS','true')
    args=SimpleNamespace(dry_run=False,no_ledger=True,dsn=None)
    with pytest.raises(RuntimeError,match='require the durable acquisition ledger'):
        enqueue.prepare_jobs(args)

def test_order_preserves_waves_and_borrows_empty_state_capacity():
    jobs=[{'state':st,'cell':i} for st,n in [('sc',70),('tn',30)] for i in range(n)]
    ordered=order_states(jobs,['sc','tn'],{'sc':7,'tn':3})
    assert sum(j['state']=='sc' for j in ordered[:50])==35
    for st in ('sc','tn'):assert [j['cell'] for j in ordered if j['state']==st]==[j['cell'] for j in jobs if j['state']==st]
    assert order_states([j for j in jobs if j['state']=='tn'],['sc','tn'])==[j for j in jobs if j['state']=='tn']

@pytest.mark.asyncio
async def test_state_refill_keeps_unfinished_campaign_after_keyword_rotation(seeded_database):
    conn=psycopg2.connect(os.environ['TEST_DATABASE_URL']);conn.autocommit=True
    ledger=SimpleNamespace(conn=conn);day=date.today()-timedelta(days=1)
    old,new='independent old fixture','independent new fixture'
    try:
        register(ledger,[old],['tn','sc'],day,'original')
        with conn.cursor() as cur:
            cur.execute("INSERT INTO keyword_yield_evaluations(keyword,state,started_on,sample_cells,full_cells,status) VALUES(%s,'sc',%s,'[]',20,'rejected')",(old,day))
        register(ledger,[new],['tn','sc'],date.today(),'next')
        result=pending(ledger,['tn','sc'])
        assert {row[0] for row in result['tn']} >= {old,new}
        assert old not in {row[0] for row in result['sc']}
        assert new in {row[0] for row in result['sc']}
        # Restart and a UTC date change preserve the original campaign date.
        register(ledger,[old],['tn','sc'],date.today(),'replayed')
        assert next(row[1] for row in pending(ledger,['tn'])['tn'] if row[0]==old)==day
        cfg={'states':['tn'],'settings':{},'queue':{},'overrides':{}}
        jobs,expected,counts=prepare(ledger,cfg,[new],date.today(),date.today(),build_jobs,eligible_jobs)
        assert {job['keyword'] for job in jobs} >= {old,new}
        assert len([j for j in jobs if j['keyword']==old])==6
    finally:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM keyword_yield_evaluations WHERE keyword=ANY(%s)",([old,new],))
            cur.execute("DELETE FROM state_campaign_backlog WHERE keyword=ANY(%s)",([old,new],))
            cur.execute("DELETE FROM approved_campaign_keywords WHERE keyword=ANY(%s)",([old,new],))
        conn.close()

@pytest.mark.asyncio
async def test_one_completed_state_can_refill_without_dropping_other_state(app_client):
    client,settings=app_client;settings.independent_state_backlogs=True
    connection=await asyncpg.connect(settings.database_url)
    try:
        settings.active_states_path.write_text('states: [tn, sc]\nsettings: {}\nqueue: {}\noverrides: {}')
        settings.keywords_path.write_text('state rollover fixture\n')
        await connection.execute("INSERT INTO approved_campaign_keywords VALUES('state rollover fixture',current_date,'test',now())")
        await connection.execute("INSERT INTO state_campaign_backlog(keyword,state,started_on) VALUES('state rollover fixture','sc',current_date),('state rollover fixture','tn',current_date)")
        await connection.execute("INSERT INTO keyword_yield_evaluations(keyword,state,started_on,sample_cells,full_cells,status) VALUES('state rollover fixture','sc',current_date,'[]',20,'rejected')")
        await connection.execute("INSERT INTO app_config(key,value,encrypted) VALUES('auto_keyword_rollover','true',false) ON CONFLICT(key) DO UPDATE SET value='true'")
        response=await client.get('/api/keywords')
        assert response.status_code==200
        assert response.json()['rollover']['state']=='ready'
        assert 'SC needs fresh keywords' in response.json()['rollover']['detail']
        # A queued job in the completed state must still drain before refill.
        await connection.execute("INSERT INTO river_job(id,kind,state,args) VALUES(985001,'scrape','available','{\"keyword\":\"state rollover fixture\",\"state\":\"sc\"}')")
        response=await client.get('/api/keywords')
        assert response.json()['rollover']['state']!='ready'
    finally:
        await connection.execute("DELETE FROM river_job WHERE id=985001")
        await connection.execute("DELETE FROM keyword_yield_evaluations WHERE keyword='state rollover fixture'")
        await connection.execute("DELETE FROM state_campaign_backlog WHERE keyword='state rollover fixture'")
        await connection.execute("DELETE FROM approved_campaign_keywords WHERE keyword='state rollover fixture'")
        await connection.close()
