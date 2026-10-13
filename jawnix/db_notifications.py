"""PostgreSQL delivers these wakeups only after the writing transaction commits."""
from sqlalchemy import event, text
from sqlalchemy.orm import Session

JOB_CHANNEL = "jawnix_jobs"
REQUEST_CHANNEL = "jawnix_requests"


@event.listens_for(Session, "after_flush")
def notify_committed_changes(session, flush_context):
    if session.get_bind().dialect.name != "postgresql":
        return
    from .jobs import job_lane
    from .models import BatchArtifact, Job, LeadRequest
    lanes = set()
    requests = set()
    for row in session.new | session.dirty:
        if isinstance(row, Job):
            if row.status == "queued":
                lanes.add(job_lane(row.kind))
            if row.request_id is not None:
                requests.add(str(row.request_id))
        elif isinstance(row, LeadRequest):
            requests.add(str(row.id))
        elif isinstance(row, BatchArtifact):
            requests.add(str(row.request_id))
    connection = session.connection()
    statement = text("SELECT pg_notify(:channel, :payload)")
    for lane in lanes:
        connection.execute(statement, {"channel":JOB_CHANNEL, "payload":lane})
    for request_id in requests:
        connection.execute(statement, {"channel":REQUEST_CHANNEL, "payload":request_id})
