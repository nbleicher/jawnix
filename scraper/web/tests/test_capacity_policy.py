from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).parents[2]/'control'))
from capacity_policy import decide

HEALTH={'healthy_heartbeats':32,'bad_heartbeats':0,'cpu_percent':40,'available_memory_gib':50}

def states(tn_busy=0,sc_busy=0,tn_queue=0,sc_queue=0):
    return {'tn':{'busy_fraction':tn_busy,'queued_productive':tn_queue,'queued_total':tn_queue},
            'sc':{'busy_fraction':sc_busy,'queued_productive':sc_queue,'queued_total':sc_queue}}

def test_tn_pressure_can_grow_fleet_even_when_sc_is_empty():
    result=decide(16,states(tn_busy=.9,tn_queue=64),HEALTH,{'saturated':1})
    assert result['target']==32

def test_empty_backlogs_do_not_scale_on_increased_phone_yield():
    assert decide(16,states(),HEALTH,{'saturated':1})['target']==16

def test_idle_capacity_releases_after_three_samples():
    assert decide(32,states(tn_busy=.1,sc_busy=.1),HEALTH,{'idle':2})['target']==16

def test_waiting_pilots_are_not_productive_backlog():
    work=states(tn_busy=.9);work['tn']['queued_total']=300
    assert decide(16,work,HEALTH,{'saturated':1})['target']==16

def test_health_pressure_prevents_scaling():
    assert decide(16,states(tn_busy=.9,tn_queue=64),{**HEALTH,'cpu_percent':90},{'saturated':1})['target']==16

def test_fleet_is_bounded_and_saturation_must_persist():
    assert decide(32,states(tn_busy=.9,tn_queue=100),HEALTH,{'saturated':2})['target']==32
    assert decide(16,states(tn_busy=.9,tn_queue=64),HEALTH,{})['target']==16
