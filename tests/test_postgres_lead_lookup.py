"""Lead lookup, phone search, and the Inventory page on real PostgreSQL with DB2.

Seam: CSV uploads published through the real two-database path in; lead
lookup's DB2 listing history, the header search, and the Inventory overview
out. The DB2 read must reach the remote primary key (upload, lead) rather
than scan every published row.
"""
import uuid

import pytest
from sqlalchemy import select, text

from jawnix.inventory import exclusion_lists_overview, inventory_overview, suppressed_leads_page
from jawnix.lead_lookup import lead_lookup
from jawnix.lead_uploads import import_lead_upload
from jawnix.models import Customer, Lead, LeadUpload
from jawnix.search import search

from test_postgres_lead_databases import DATABASE_URL, stores, upload  # noqa: F401

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="requires disposable PostgreSQL")


def test_lookup_reads_every_published_db2_row_for_the_phone(stores, tmp_path):  # noqa: F811
    settings, factory, owner = stores
    phone = f"615{uuid.uuid4().int % 10**7:07d}"
    with factory.begin() as db:
        db.add(Lead(phone=phone, title="Operator listing", state="TN"))
        db.add(Customer(slug=str(uuid.uuid4()), name="CSV recipient", lead_database="db2", licensed_states=["TN"]))
    first = upload(factory, owner, tmp_path / "first.csv", f"Phone,Name\n{phone},First title\n".encode())
    assert import_lead_upload(first, settings, session_factory=factory)["status"] == "complete"
    second = upload(factory, owner, tmp_path / "second.csv", f"Phone,Name\n{phone},Second title\n".encode())
    assert import_lead_upload(second, settings, session_factory=factory)["status"] == "complete"

    with factory() as db:
        result = lead_lookup(db, settings, phone=phone)
        db2 = sorted(
            (row["source"], row["title"]) for row in result["listings"] if row["database"] == "db2"
        )
        assert db2 == [
            ("DB2 · first.csv (Alex fixture)", "First title"),
            ("DB2 · second.csv (Alex fixture)", "Second title"),
        ]
        assert result["databases"]["db2"]["upload"]["filename"] == "second.csv"
        assert [row["key"] for row in result["nextRecipients"]] == ["now"]

        lead_id = result["lead"]["id"]
        uploads = list(db.scalars(select(LeadUpload.id)))
        plan = "\n".join(
            db.scalars(
                text(
                    "EXPLAIN VERBOSE SELECT title FROM db2_inventory_rows "
                    "WHERE upload_id = ANY(:uploads) AND lead_id = :lead"
                ),
                {"uploads": uploads, "lead": lead_id},
            )
        )
        # Both predicates travel to DB2, where the primary key serves them.
        assert "Remote SQL" in plan
        remote = plan.split("Remote SQL:", 1)[1].split("WHERE", 1)[1]
        assert "lead_id" in remote and "upload_id" in remote

        found = search(db, phone)
        assert [group["key"] for group in found["groups"]] == ["lead", "leadUpload"]
        assert found["groups"][0]["results"][0]["detail"] == "DB1 and DB2 · usable"
        assert found["groups"][1]["results"][0]["title"] == "second.csv"


def test_inventory_reads_run_on_postgres(stores):  # noqa: F811
    _, factory, _ = stores
    with factory.begin() as db:
        db.add(Lead(phone=f"212{uuid.uuid4().int % 10**7:07d}", title="Gone", state="NY", suppressed=True))
    with factory() as db:
        overview = inventory_overview(db)
        assert overview["sections"]["suppressed"] == 1
        assert overview["lastUpload"] is None
        assert suppressed_leads_page(db)["total"] == 1
        assert exclusion_lists_overview(db)["lists"]["total"] == 0
