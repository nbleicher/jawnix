"""Acquire exact, globally deduplicated endpoint yield without changing inventory."""
from __future__ import annotations

import json
import os
from urllib.request import Request, urlopen


class LookupUnavailable(RuntimeError):
    pass


def membership(rows, *, lookup_url=None, token=None):
    candidates = [{"phone":str(row[4] or "")[:80],"title":str(row[3] or "")[:1000],
                   "state":str(row[13] or "").upper()[:2]} for row in rows if row[4]]
    if not candidates:
        return set(), set()
    lookup_url = lookup_url or os.environ.get("JAWNIX_PHONE_LOOKUP_URL", "")
    token = token or os.environ.get("JAWNIX_PHONE_LOOKUP_TOKEN", "")
    if not lookup_url or not token:
        raise LookupUnavailable("Canonical phone membership lookup is not configured")
    valid, existing = set(), set()
    for start in range(0,len(candidates),2000):
        request = Request(lookup_url,data=json.dumps({"candidates":candidates[start:start+2000]}).encode(),
                          headers={"Content-Type":"application/json","Authorization":"Bearer "+token},method="POST")
        try:
            with urlopen(request,timeout=10) as response:
                data=json.load(response)
        except Exception:
            # Keep credentials, request phones, and provider response bodies out of logs.
            raise LookupUnavailable("Canonical phone membership lookup unavailable") from None
        if not isinstance(data,dict):
            raise LookupUnavailable("Canonical phone membership lookup returned invalid evidence")
        v,e=data.get("valid_phones"),data.get("existing_phones")
        if not isinstance(v,list) or not isinstance(e,list) or any(
            not isinstance(phone,str) or len(phone)!=10 or not phone.isascii() or not phone.isdigit()
            for phone in [*v,*e]
        ) or not set(e)<=set(v):
            raise LookupUnavailable("Canonical phone membership lookup returned invalid evidence")
        valid.update(v);existing.update(e)
    return valid, existing


def claim_new_phones(cur, job_id, valid, existing):
    """Replay and concurrent jobs credit an endpoint to exactly one original job."""
    eligible=sorted(valid-existing)
    if eligible:
        cur.execute("""INSERT INTO acquisition_phone_registry(phone,first_job_id)
            SELECT phone,%s FROM unnest(%s::text[]) phone
            ON CONFLICT DO NOTHING""",(job_id,eligible))
    cur.execute("SELECT count(*) FROM acquisition_phone_registry WHERE first_job_id=%s",(job_id,))
    return int(cur.fetchone()[0])
