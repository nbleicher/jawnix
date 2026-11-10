"""The operator's DB2 CSV uploads.

Managed administrators once uploaded here too. Their access is retired, but
their uploads remain in DB2 and still name them as the uploader.
"""
from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from .activity import record_activity
from .auth import Principal, require_admin
from .config import Settings, get_settings
from .database import get_db
from .jobs import enqueue_job
from .lead_uploads import IMPORT_JOB, upload_record
from .models import LeadDatabaseSummary, LeadUpload, ManagedAdmin
from .states import US_STATES, US_STATE_OPTIONS

router = APIRouter()


def _upload_workspace(db, records: list[dict]) -> dict:
    summary = db.get(LeadDatabaseSummary, "db2")
    return {"uploads":records, "states":[{"code":code, "name":name} for code,name in US_STATE_OPTIONS],
            "inventory":{"total":summary.total if summary else 0, "states":summary.states if summary else {}, "asOf":summary.updated_at if summary else None}}


def _rejected_rows(item: LeadUpload) -> FileResponse:
    path = Path(item.path).with_suffix(".errors.csv")
    if item.status not in {"complete", "failed"} or not path.is_file():
        raise HTTPException(status_code=404, detail="Rejected-row report is unavailable.")
    return FileResponse(path, media_type="text/csv", filename="rejected-leads.csv", headers={"Cache-Control":"private, no-store"})


def _retry_upload(db, item: LeadUpload) -> None:
    db.refresh(item, with_for_update=True)
    if item.status != "failed":
        raise HTTPException(status_code=409, detail="Only failed uploads can be retried.")
    item.status, item.error = "queued", ""
    enqueue_job(db, IMPORT_JOB, payload={"upload_id":str(item.id)})
    db.commit()


async def _receive_upload(request: Request, db: Session, settings: Settings, *, verify_access, owner: dict, action: str) -> LeadUpload:
    # Authenticate before parsing multipart data. UploadFile spools large files
    # to disk; neither the API nor the importer loads the full CSV into memory.
    length = request.headers.get("Content-Length")
    if length:
        try:
            bytes_expected = int(length)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid upload length.")
        if bytes_expected > settings.lead_upload_max_bytes + 65536:
            raise HTTPException(status_code=413, detail="CSV exceeds the upload byte limit.")
    db.close()
    async with request.form(max_files=1, max_fields=1) as form:
        file = form.get("file")
        state = str(form.get("state") or "").strip().upper()
        if not isinstance(file, UploadFile) or not (file.filename or "").lower().endswith(".csv"):
            raise HTTPException(status_code=422, detail="Choose a CSV file.")
        if state not in US_STATES:
            raise HTTPException(status_code=422, detail="Choose a US state for this file.")
        if file.size is not None and file.size > settings.lead_upload_max_bytes:
            raise HTTPException(status_code=413, detail="CSV exceeds the upload byte limit.")
        upload_id = uuid.uuid4()
        directory = settings.batch_dir / "lead-uploads"
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = directory / f"{upload_id}.csv"
        partial = path.with_suffix(".partial")
        checksum, size = hashlib.sha256(), 0
        try:
            with os.fdopen(os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as output:
                while block := await file.read(1024*1024):
                    size += len(block)
                    if size > settings.lead_upload_max_bytes:
                        raise HTTPException(status_code=413, detail="CSV exceeds the upload byte limit.")
                    checksum.update(block)
                    output.write(block)
            if size == 0:
                raise HTTPException(status_code=422, detail="The CSV is empty.")
            # Recheck live access after a potentially long file transfer.
            await verify_access()
            os.replace(partial, path)
            item = LeadUpload(id=upload_id, **owner, filename=Path(file.filename).name[:255],
                              state=state, status="queued", path=str(path), sha256=checksum.hexdigest(), bytes=size)
            db.add(item)
            record_activity(db, action=action, target_type="lead_upload", target_id=upload_id,
                            actor_id=item.uploader_id, reason="CSV submitted for DB2 ingestion",
                            details={"state":state, "bytes":size, "sha256":item.sha256})
            enqueue_job(db, IMPORT_JOB, payload={"upload_id":str(upload_id)})
            db.commit()
            return item
        except BaseException:
            partial.unlink(missing_ok=True)
            db.rollback()
            try:
                recorded = db.get(LeadUpload, upload_id)
            except Exception:
                # A lost commit response must not delete a potentially
                # recorded source file. The operator can reconcile it.
                recorded = True
            if recorded is None:
                path.unlink(missing_ok=True)
            raise


# Every upload adds to one DB2 inventory. Uploads from the retired managed
# administrators keep their uploader's name.
def _operator_record(db, item: LeadUpload) -> dict:
    manager = db.get(ManagedAdmin, item.manager_id) if item.manager_id else None
    return {**upload_record(item), "uploadedBy":manager.name if manager else "Operator"}


def _any_upload(db, upload_id):
    item = db.get(LeadUpload, upload_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Upload was not found.")
    return item


@router.get("/api/admin/lead-uploads")
def operator_uploads(principal: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.scalars(select(LeadUpload).order_by(LeadUpload.created_at.desc()).limit(50))
    return _upload_workspace(db, [_operator_record(db, row) for row in rows])


@router.get("/api/admin/lead-uploads/{upload_id}")
def operator_upload_status(upload_id: uuid.UUID, principal: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    return _operator_record(db, _any_upload(db, upload_id))


@router.get("/api/admin/lead-uploads/{upload_id}/rejected")
def operator_rejected_rows(upload_id: uuid.UUID, principal: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    return _rejected_rows(_any_upload(db, upload_id))


@router.post("/api/admin/lead-uploads/{upload_id}/retry")
def retry_operator_upload(upload_id: uuid.UUID, principal: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    item = _any_upload(db, upload_id)
    _retry_upload(db, item)
    return _operator_record(db, item)


@router.post("/api/admin/lead-uploads", status_code=202)
async def submit_operator_upload(request: Request, principal: Principal = Depends(require_admin), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    async def verify_access():
        await require_admin(principal, settings, db)
    item = await _receive_upload(request, db, settings, verify_access=verify_access,
                                 owner={"operator_id":principal.user_id}, action="operator_lead_upload_submitted")
    return _operator_record(db, item)
