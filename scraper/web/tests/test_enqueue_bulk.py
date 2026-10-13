from datetime import date
from pathlib import Path
import io
import json
import sys
from urllib.error import URLError

import pytest

sys.path.insert(0,str(Path(__file__).parents[2]/"control"))
import enqueue


class Ledger:
    def queue_depth(self):
        return 0

    def reserve(self,*args):
        raise AssertionError("bulk enqueue must not reserve outside its transaction")

    def mark_posted(self,*args):
        raise AssertionError("bulk enqueue must commit posted status with the jobs")


def jobs(count):
    return [{"keyword":"Plumbers","state":"tn","geo_coordinates":f"{35+n/1000},-86"} for n in range(count)]


def test_bulk_refill_bounds_requests_and_preserves_order(monkeypatch):
    calls=[]
    def insert(api,batch,day,expected,key):
        calls.append(batch)
        return len(batch),None
    monkeypatch.setattr(enqueue,"insert_batch",insert)
    candidates=jobs(250)
    assert enqueue.top_up("http://queue.test",candidates,0,250,1000,Ledger(),date.today(),{}) == (250,250,0)
    assert list(map(len,calls)) == [100,100,50]
    assert [job for batch in calls for job in batch] == candidates


def test_lost_response_retains_batch_for_replay(monkeypatch):
    candidates=jobs(100)
    calls=[]
    def insert(api,batch,*args):
        calls.append(batch)
        return (None,"response lost") if len(calls)==1 else (0,None)
    monkeypatch.setattr(enqueue,"insert_batch",insert)
    arguments=("http://queue.test",candidates,0,100,100,Ledger(),date.today(),{})
    assert enqueue.top_up(*arguments) == (0,0,0)
    # The server has already committed the first attempt, so the replay skips it.
    assert enqueue.top_up(*arguments) == (100,0,0)
    assert calls[0] == calls[1] == candidates


def test_pause_stops_before_the_next_batch(tmp_path,monkeypatch):
    pause=tmp_path/"paused"
    calls=[]
    def insert(api,batch,*args):
        calls.append(batch)
        pause.touch()
        return len(batch),None
    monkeypatch.setattr(enqueue,"insert_batch",insert)
    assert enqueue.top_up("http://queue.test",jobs(200),0,200,200,Ledger(),date.today(),{},pause_file=pause) == (100,100,0)
    assert len(calls)==1


def test_bulk_http_contract_keeps_day_key_and_coverage(monkeypatch):
    captured=[]
    class Response(io.BytesIO):
        status=202
    def open_request(request,timeout):
        captured.append(request)
        return Response(b'{"inserted":1,"skipped":0}')
    monkeypatch.setattr(enqueue,"urlopen",open_request)
    candidate={**jobs(1)[0],"_internal":"ignored"}
    day=date(2026,10,13)
    assert enqueue.insert_batch("http://queue.test",[candidate],day,{("Plumbers","tn"):156},"fixture") == (1,None)
    request=captured[0]
    assert request.full_url == "http://queue.test/api/v1/scrape/bulk"
    payload=json.loads(request.data)
    assert payload["day"] == str(day)
    assert payload["jobs"][0]["expected_cells"] == 156
    assert "_internal" not in payload["jobs"][0]
    assert request.get_header("X-api-key") == "fixture"


@pytest.mark.parametrize("body",[b'{"inserted":0,"skipped":0}',b'{"inserted":true,"skipped":0}',b'[]',b'invalid'])
def test_invalid_response_does_not_advance_batch(body,monkeypatch):
    class Response(io.BytesIO):
        status=202
    monkeypatch.setattr(enqueue,"urlopen",lambda *args,**kwargs:Response(body))
    inserted,error=enqueue.insert_batch("http://queue.test",jobs(1),date.today(),{})
    assert inserted is None and error
