"""Bounded CSV ingestion and atomic publication of the DB2 eligibility index."""
from __future__ import annotations

import csv
import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError

from .activity import record_activity
from .database import SessionLocal
from .db2_setup import db2_url
from .job_wakeup import postgres_dsn
from .jobs import enqueue_job
from .lead_databases import db2_inventory
from .models import Customer, CustomerAvailabilitySnapshot, Job, LeadDatabaseMembership, LeadDatabaseSummary, LeadUpload, utcnow
from .states import normalize_phone

IMPORT_JOB = "import_lead_upload"
CHUNK_ROWS = 5000
CHUNK_BYTES = 8 * 1024 * 1024
PHONE_HEADERS = {"phone", "phonenumber", "telephone", "telephonenumber", "mobile", "mobilenumber", "cell", "cellphone", "number"}
TITLE_HEADERS = {"title", "name", "fullname", "businessname", "company", "companyname"}


class InvalidCSV(ValueError):
    pass


class ImportInterrupted(RuntimeError):
    pass


@dataclass
class Progress:
    processed: int = 0
    valid: int = 0
    rejected: int = 0


def upload_record(item: LeadUpload) -> dict:
    return {
        "id":str(item.id), "filename":item.filename, "state":item.state, "status":item.status,
        "bytes":item.bytes, "processedRows":item.processed_rows, "validRows":item.valid_rows,
        "rejectedRows":item.rejected_rows, "importedRows":item.imported_rows,
        "updatedRows":item.updated_rows, "duplicateRows":item.duplicate_rows,
        "error":item.error, "createdAt":item.created_at, "completedAt":item.completed_at,
    }


def csv_chunks(path: Path, progress: Progress, errors_path: Path):
    """Stream arbitrary row counts. Bound a chunk by both row count and bytes."""
    csv.field_size_limit(16 * 1024 * 1024)
    with path.open(encoding="utf-8-sig", newline="") as source, errors_path.open("w", encoding="utf-8", newline="") as errors:
        report = csv.writer(errors)
        report.writerow(["row", "phone", "reason"])
        sample = source.read(65536)
        source.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(source, dialect=dialect)
        fields = reader.fieldnames or []
        normalized = {name:re.sub(r"[^a-z0-9]", "", name.lower()) for name in fields}
        phone_column = next((name for name in fields if normalized[name] in PHONE_HEADERS), None)
        title_column = next((name for name in fields if normalized[name] in TITLE_HEADERS), None)
        if phone_column is None:
            raise InvalidCSV("The CSV must contain a Phone or Phone Number column.")
        rows, size = [], 0
        for number, row in enumerate(reader, start=2):
            progress.processed += 1
            raw_phone = row.get(phone_column) or ""
            phone = normalize_phone(raw_phone)
            title = str(row.get(title_column) or "") if title_column else ""
            reason = "Phone must normalize to a ten-digit US number." if phone is None else ("Name contains a null byte." if "\0" in title else "")
            if reason:
                progress.rejected += 1
                # Prefix formula-like input in the downloadable error report.
                safe_phone = "'"+raw_phone if raw_phone.startswith(("=", "+", "-", "@")) else raw_phone
                report.writerow([number, safe_phone, reason])
            else:
                progress.valid += 1
                rows.append((phone, title, number))
                size += len(title.encode("utf-8")) + 32
            if len(rows) >= CHUNK_ROWS or size >= CHUNK_BYTES or progress.processed % CHUNK_ROWS == 0:
                yield rows
                rows, size = [], 0
        if rows or progress.processed % CHUNK_ROWS:
            yield rows


def _canonical_ids(session, phones):
    """Register endpoints without adding any listing record to DB1."""
    driver = session.connection().connection.driver_connection
    with driver.cursor() as cursor:
        cursor.execute("CREATE TEMP TABLE csv_registry_chunk (phone varchar(10) PRIMARY KEY) ON COMMIT DROP")
        with cursor.copy("COPY csv_registry_chunk (phone) FROM STDIN") as copy:
            for phone in phones:
                copy.write_row((phone,))
        cursor.execute("""INSERT INTO lead_inventory
            (phone, title, state, first_seen_at, source_flow, legacy_title, legacy_state, suppressed, suppression_reason, in_db1)
            SELECT phone, '', '', now(), 'db2_registry', '', '', false, '', false FROM csv_registry_chunk
            ON CONFLICT (phone) DO NOTHING""")
        cursor.execute("SELECT l.phone,l.id FROM lead_inventory l JOIN csv_registry_chunk c ON c.phone=l.phone")
        return dict(cursor.fetchall())


def _retain_chunk(source, upload, rows, progress, factory, job_id):
    unique = {row[0]:row for row in rows}
    with factory.begin() as session:
        ids = _canonical_ids(session, unique) if unique else {}
    with source.transaction():
        source.execute("CREATE TEMP TABLE csv_import_chunk (lead_id bigint, phone varchar(10), title text, row_number bigint) ON COMMIT DROP")
        with source.cursor().copy("COPY csv_import_chunk FROM STDIN") as copy:
            for phone, title, number in unique.values():
                copy.write_row((ids[phone], phone, title, number))
        source.execute("""INSERT INTO inventory_rows (upload_id,lead_id,phone,title,state,row_number)
            SELECT %s,lead_id,phone,title,%s,row_number FROM csv_import_chunk
            ON CONFLICT (upload_id,lead_id) DO UPDATE SET title=EXCLUDED.title,row_number=EXCLUDED.row_number""", (upload.id, upload.state))
        source.execute("UPDATE uploads SET processed_rows=%s,valid_rows=%s,rejected_rows=%s WHERE id=%s",
                       (progress.processed, progress.valid, progress.rejected, upload.id))
    with factory.begin() as session:
        session.execute(update(LeadUpload).where(LeadUpload.id == upload.id).values(
            processed_rows=progress.processed, valid_rows=progress.valid, rejected_rows=progress.rejected))
        if job_id is not None:
            session.execute(update(Job).where(Job.id == job_id).values(locked_at=utcnow()))


def _publish(upload_id, factory, progress):
    with factory.begin() as session:
        item = session.scalar(select(LeadUpload).where(LeadUpload.id == upload_id).with_for_update())
        if item.status == "complete":
            return upload_record(item)
        source = select(db2_inventory.c.lead_id, db2_inventory.c.upload_id, db2_inventory.c.state).where(db2_inventory.c.upload_id == upload_id)
        total = int(session.scalar(select(func.count()).select_from(db2_inventory).where(db2_inventory.c.upload_id == upload_id)) or 0)
        updated = int(session.scalar(select(func.count()).select_from(LeadDatabaseMembership).where(
            LeadDatabaseMembership.lead_id.in_(select(db2_inventory.c.lead_id).where(db2_inventory.c.upload_id == upload_id))
        )) or 0)
        statement = pg_insert(LeadDatabaseMembership).from_select(["lead_id", "upload_id", "state"], source)
        session.execute(statement.on_conflict_do_update(index_elements=["lead_id"], set_={
            "upload_id":statement.excluded.upload_id, "state":statement.excluded.state}))
        states = dict(session.execute(select(LeadDatabaseMembership.state, func.count()).group_by(LeadDatabaseMembership.state)).all())
        session.merge(LeadDatabaseSummary(id="db2", total=sum(states.values()), states=states, updated_at=utcnow()))
        session.execute(delete(CustomerAvailabilitySnapshot).where(CustomerAvailabilitySnapshot.customer_id.in_(
            select(Customer.id).where(Customer.lead_database == "db2"))))
        item.status = "complete"
        item.error = ""
        item.processed_rows, item.valid_rows, item.rejected_rows = progress.processed, progress.valid, progress.rejected
        item.imported_rows, item.updated_rows = total-updated, updated
        item.duplicate_rows = progress.valid-total
        item.completed_at = utcnow()
        record_activity(session, action="manager_lead_upload_published", target_type="lead_upload", target_id=item.id,
                        actor_id=item.manager_id, reason="Complete CSV inventory published to DB2",
                        details={"state":item.state, "processed":progress.processed, "imported":item.imported_rows,
                                 "updated":updated, "rejected":progress.rejected, "duplicates":item.duplicate_rows,
                                 "sha256":item.sha256})
        enqueue_job(session, "fulfill_round_robin")
        session.flush()
        return upload_record(item)


def import_lead_upload(upload_id: uuid.UUID, settings, *, session_factory=SessionLocal, job_id=None):
    """Retry from the source file or a completed DB2 version, never publish a prefix."""
    factory = session_factory
    with factory.begin() as session:
        upload = session.get(LeadUpload, upload_id)
        if upload is None:
            raise LookupError("CSV upload was not found.")
        if upload.status == "complete":
            return upload_record(upload)
        upload.status, upload.error = "processing", ""
        session.flush()
        session.expunge(upload)
    progress = Progress()
    try:
        with psycopg.connect(postgres_dsn(db2_url(settings)), autocommit=True) as source:
            existing = source.execute("SELECT status,processed_rows,valid_rows,rejected_rows,sha256 FROM uploads WHERE id=%s", (upload.id,)).fetchone()
            if existing and existing[4] != upload.sha256:
                raise InvalidCSV("The stored CSV does not match the submitted upload.")
            if existing and existing[0] == "ready":
                progress = Progress(*existing[1:4])
            else:
                source.execute("INSERT INTO uploads (id,manager_id,state,sha256) VALUES (%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING",
                               (upload.id, upload.manager_id, upload.state, upload.sha256))
                checksum = hashlib.sha256()
                with Path(upload.path).open("rb") as stored:
                    for block in iter(lambda: stored.read(1024*1024), b""):
                        checksum.update(block)
                if checksum.hexdigest() != upload.sha256:
                    raise InvalidCSV("The stored CSV does not match the submitted upload.")
                for rows in csv_chunks(Path(upload.path), progress, Path(upload.path).with_suffix(".errors.csv")):
                    _retain_chunk(source, upload, rows, progress, factory, job_id)
                if progress.processed == 0:
                    raise InvalidCSV("The CSV contains no lead records.")
                if progress.valid == 0:
                    raise InvalidCSV("The CSV contains no valid phone records. Review the rejected rows.")
                source.execute("UPDATE uploads SET status='ready',processed_rows=%s,valid_rows=%s,rejected_rows=%s WHERE id=%s",
                               (progress.processed, progress.valid, progress.rejected, upload.id))
        return _publish(upload_id, factory, progress)
    except (InvalidCSV, UnicodeDecodeError, csv.Error, OSError) as error:
        message = str(error) if isinstance(error, InvalidCSV) else "The CSV could not be read. Use UTF-8 text with a Phone column."
        with factory.begin() as session:
            item = session.get(LeadUpload, upload_id)
            item.status, item.error = "failed", message
            item.processed_rows, item.valid_rows, item.rejected_rows = progress.processed, progress.valid, progress.rejected
            return upload_record(item)
    except (psycopg.Error, DBAPIError) as error:
        raise ImportInterrupted("CSV import was interrupted by a database connection error.") from error
