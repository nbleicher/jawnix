"""Scale the shared fleet against productive backlog and measured busy time."""
from __future__ import annotations

def decide(current,states,health,history,*,minimum=8,maximum=32):
    """Two saturated samples grow capacity; three idle samples release it."""
    busy=sum(item['busy_fraction'] for item in states.values())
    productive=sum(item['queued_productive'] for item in states.values())
    all_queued=sum(item['queued_total'] for item in states.values())
    safe=(health['bad_heartbeats']==0 and health['healthy_heartbeats']>=current
          and health['cpu_percent']<80 and health['available_memory_gib']>15)
    saturated=busy>=.8 and productive>=current and safe
    idle=busy<.35 and all_queued<current
    samples={'saturated':history.get('saturated',0)+1 if saturated else 0,
             'idle':history.get('idle',0)+1 if idle else 0}
    target=current
    reason='Hold capacity'
    if samples['saturated']>=2 and current<maximum:
        target=min(maximum,current*2);reason='Productive jobs waiting while workers are busy'
    elif samples['idle']>=3 and current>minimum:
        target=max(minimum,current//2);reason='Spare workers and insufficient queued work'
    if target!=current:samples={'saturated':0,'idle':0}
    return {'target':target,'reason':reason,'samples':samples,'states':states,'busy_fraction':busy,
            'queued_productive':productive,'queued_total':all_queued}
