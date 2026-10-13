from datetime import datetime, timezone

from sqlalchemy import event, select

from conftest import make_request
from jawnix.allocation import allocate_request
from jawnix.models import Agent, DistributionEvent, Lead, ListingObservation


def test_allocation_bulk_reads_provenance_and_preserves_snapshots(session, settings):
    customer = Agent(slug="bulk-provenance", name="Bulk provenance")
    session.add(customer)
    session.flush()
    leads = [Lead(phone=f"469555{index:04d}", title=f"Listing {index}", state="TX") for index in range(24)]
    session.add_all(leads)
    session.flush()
    observations = [ListingObservation(lead_id=lead.id, dataset_checksum="a"*64, row_number=index+1, title=lead.title, state="TX", source="TX::Roofing", niche="Roofing", valid=True, observed_at=datetime.now(timezone.utc)) for index,lead in enumerate(leads)]
    session.add_all(observations)
    session.flush()
    expected = {}
    for lead, observation in zip(leads, observations):
        lead.current_listing_observation_id = observation.id
        expected[lead.id] = {"kind":"current_listing", "observationId":observation.id, "datasetChecksum":"a"*64, "source":"TX::Roofing", "niche":"Roofing", "datasetVersion":None, "scrapeRunId":None, "configurationId":None, "configurationVersion":None}
    request = make_request(session, customer, len(leads))
    request_id = request.id
    session.commit()
    session.expunge_all()
    statements = []
    def counted(conn,cursor,statement,*args): statements.append(statement)
    event.listen(session.get_bind(), "before_cursor_execute", counted)
    try:
        result = allocate_request(session, request_id, settings)
    finally:
        event.remove(session.get_bind(), "before_cursor_execute", counted)
    assert result.allocated == len(expected)
    records = list(session.scalars(select(DistributionEvent).where(DistributionEvent.request_id == request_id)))
    assert {row.lead_id:row.listing_provenance for row in records} == expected
    assert all(row.source_niche == "roofing" for row in records)
    reads = [sql for sql in statements if sql.startswith("SELECT") and "FROM listing_observations" in sql]
    assert len(reads) <= 1, f"Allocation performed {len(reads)} listing reads"
    publications = [sql for sql in statements if sql.startswith("SELECT") and "FROM dataset_publications" in sql]
    assert len(publications) <= 1, f"Allocation performed {len(publications)} publication reads"
