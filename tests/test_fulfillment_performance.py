from datetime import datetime, timedelta, timezone

from sqlalchemy import event

from jawnix.fulfillment import workspace
from jawnix.models import BatchArtifact, DistributionEvent, Lead
from test_fulfillment_api import customer_with_request


def test_expired_batches_use_bounded_reads(session, tmp_path):
    """More expired batches must not add one count query per batch."""
    for index in range(12):
        _, _, request = customer_with_request(session, status="delivered")
        lead = Lead(phone=f"469555{index:04d}", title="Fixture", state="TX")
        session.add(lead)
        session.flush()
        session.add(DistributionEvent(lead_id=lead.id, agent_id=request.agent_id, request_id=request.id, phone=lead.phone, title=lead.title, state=lead.state))
        session.add(BatchArtifact(request_id=request.id, path=str(tmp_path / f"missing-{index}.zip"), filename=f"batch-{index}.zip", row_count=1, byte_count=1, sha256="a"*64, expires_at=datetime.now(timezone.utc)-timedelta(days=1)))
    session.commit()
    session.expunge_all()
    statements = []
    def counted(conn, cursor, statement, *args): statements.append(statement)
    event.listen(session.get_bind(), "before_cursor_execute", counted)
    try:
        result = workspace(session)
    finally:
        event.remove(session.get_bind(), "before_cursor_execute", counted)
    assert len(result["expiredArtifacts"]) == 12
    assert all(row["actions"][0]["name"] == "regenerate" for row in result["expiredArtifacts"])
    counts = [sql for sql in statements if "count(" in sql and "FROM distribution_events" in sql]
    assert len(counts) <= 2, f"Expired batches caused {len(counts)} distribution counts"
    assert len(statements) <= 20, f"Workspace made {len(statements)} SQL calls"


def test_overview_skips_expired_history(session, monkeypatch):
    from jawnix import fulfillment
    def unexpected(*args, **kwargs): raise AssertionError("Overview loaded expired history")
    monkeypatch.setattr(fulfillment, "_expired_artifacts", unexpected)
    assert workspace(session, include_expired=False)["expiredArtifacts"] == []
