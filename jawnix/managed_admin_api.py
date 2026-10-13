"""Customer-scoped administration, CSV uploads, and operator inventory routing."""
from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from .activity import record_activity
from .auth import Principal, require_admin, require_manager
from .config import Settings, get_settings
from .database import get_db
from .jobs import enqueue_job
from .lead_uploads import IMPORT_JOB, upload_record
from .mfa_provider import BetterAuthMFAProvider, get_mfa_provider
from .models import Customer, CustomerAvailabilitySnapshot, LeadDatabaseSummary, LeadUpload, ManagedAdmin, UserAccount
from .states import US_STATES, US_STATE_OPTIONS

router = APIRouter()


class CustomerScopeChange(BaseModel):
    lead_database: Literal["db1", "db2"] | None = None
    allowed_manager_id: uuid.UUID | None = None
    manager_id: uuid.UUID | None = None


class AddManagedCustomers(BaseModel):
    customer_ids: list[int] = Field(min_length=1, max_length=1000)


def customer_scope(item: Customer) -> dict:
    return {"id":item.id, "lead_database":item.lead_database,
            "allowed_manager_id":str(item.allowed_manager_id) if item.allowed_manager_id else None,
            "manager_id":str(item.manager_id) if item.manager_id else None}


def _manager_customer_rows(db, manager_id):
    rows = db.execute(select(Customer, UserAccount.email)
                      .outerjoin(UserAccount, (UserAccount.customer_id == Customer.id) & UserAccount.active.is_(True))
                      .where(Customer.allowed_manager_id == manager_id, Customer.deleted_at.is_(None))
                      .order_by(Customer.name, Customer.id)).all()
    return [{**customer_scope(item), "name":item.name, "email":email or "", "active":item.active,
             "licensed_states":list(item.licensed_states or [])} for item, email in rows]


@router.get("/api/manager/access")
def manager_access(principal: Principal = Depends(require_manager)):
    return {"ok":True, "id":str(principal.user_id), "email":principal.email}


@router.get("/api/manager/customers")
def managed_customers(principal: Principal = Depends(require_manager), db: Session = Depends(get_db)):
    rows = _manager_customer_rows(db, principal.user_id)
    return {"customers":[row for row in rows if row["manager_id"] == str(principal.user_id)],
            "available":[row for row in rows if row["manager_id"] is None and row["active"]]}


@router.post("/api/manager/customers")
def add_managed_customers(payload: AddManagedCustomers, principal: Principal = Depends(require_manager), db: Session = Depends(get_db)):
    ids = set(payload.customer_ids)
    rows = list(db.scalars(select(Customer).where(Customer.id.in_(ids)).with_for_update()))
    if len(rows) != len(ids) or any(item.allowed_manager_id != principal.user_id or not item.active or item.deleted_at is not None for item in rows):
        raise HTTPException(status_code=404, detail="A selected Customer is unavailable.")
    for item in rows:
        if item.manager_id != principal.user_id:
            item.manager_id = principal.user_id
            record_activity(db, action="customer_manager_assigned", target_type="customer", target_id=item.id,
                            actor_id=principal.user_id, reason="Managed administrator selected an operator-approved Customer",
                            details={"managerId":str(principal.user_id)})
    db.commit()
    return managed_customers(principal, db)


@router.patch("/api/admin/customers/{customer_id}/scope")
def update_customer_scope(customer_id: int, payload: CustomerScopeChange, principal: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    item = db.scalar(select(Customer).where(Customer.id == customer_id, Customer.deleted_at.is_(None)).with_for_update())
    if item is None:
        raise HTTPException(status_code=404, detail="Customer was not found.")
    before = customer_scope(item)
    fields = payload.model_fields_set
    if "lead_database" in fields:
        if payload.lead_database is None:
            raise HTTPException(status_code=422, detail="Choose DB1 or DB2.")
        item.lead_database = payload.lead_database
    if "allowed_manager_id" in fields:
        manager = db.get(ManagedAdmin, payload.allowed_manager_id) if payload.allowed_manager_id else None
        if payload.allowed_manager_id and (manager is None or not manager.active):
            raise HTTPException(status_code=422, detail="Managed administrator is unavailable.")
        item.allowed_manager_id = payload.allowed_manager_id
        if item.manager_id != item.allowed_manager_id:
            item.manager_id = None
    if "manager_id" in fields:
        if payload.manager_id is not None and payload.manager_id != item.allowed_manager_id:
            raise HTTPException(status_code=422, detail="Make this Customer available to the administrator first.")
        item.manager_id = payload.manager_id
    after = customer_scope(item)
    if after != before:
        record_activity(db, action="customer_inventory_scope_changed", target_type="customer", target_id=item.id,
                        actor_id=principal.user_id, reason="Operator changed Customer inventory or managed administration",
                        details={"before":before, "after":after})
        if before["lead_database"] != after["lead_database"]:
            db.execute(delete(CustomerAvailabilitySnapshot).where(CustomerAvailabilitySnapshot.customer_id == item.id))
            enqueue_job(db, "fulfill_round_robin")
    db.commit()
    return after


@router.post("/api/admin/managed-admins/{manager_id}/access-link")
async def managed_admin_access_link(manager_id: uuid.UUID, principal: Principal = Depends(require_admin), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    manager = db.get(ManagedAdmin, manager_id)
    if manager is None or not manager.active:
        raise HTTPException(status_code=404, detail="Managed administrator was not found.")
    provider = get_mfa_provider(settings)
    if not isinstance(provider, BetterAuthMFAProvider):
        raise HTTPException(status_code=503, detail="Managed administrator onboarding requires Better Auth.")
    result = await provider.request("manager-access-link", userId=str(manager_id))
    record_activity(db, action="manager_access_link_created", target_type="managed_admin", target_id=manager_id,
                    actor_id=principal.user_id, reason="Operator generated a password setup link", details={"expiresIn":result["expiresIn"]})
    db.commit()
    return result


@router.get("/api/manager/uploads")
def managed_uploads(principal: Principal = Depends(require_manager), db: Session = Depends(get_db)):
    rows = db.scalars(select(LeadUpload).where(LeadUpload.manager_id == principal.user_id).order_by(LeadUpload.created_at.desc()).limit(50))
    summary = db.get(LeadDatabaseSummary, "db2")
    return {"uploads":[upload_record(row) for row in rows], "states":[{"code":code, "name":name} for code,name in US_STATE_OPTIONS],
            "inventory":{"total":summary.total if summary else 0, "states":summary.states if summary else {}, "asOf":summary.updated_at if summary else None}}


def _owned_upload(db, upload_id, manager_id):
    item = db.scalar(select(LeadUpload).where(LeadUpload.id == upload_id, LeadUpload.manager_id == manager_id))
    if item is None:
        raise HTTPException(status_code=404, detail="Upload was not found.")
    return item


@router.get("/api/manager/uploads/{upload_id}")
def managed_upload_status(upload_id: uuid.UUID, principal: Principal = Depends(require_manager), db: Session = Depends(get_db)):
    return upload_record(_owned_upload(db, upload_id, principal.user_id))


@router.get("/api/manager/uploads/{upload_id}/rejected")
def rejected_upload_rows(upload_id: uuid.UUID, principal: Principal = Depends(require_manager), db: Session = Depends(get_db)):
    item = _owned_upload(db, upload_id, principal.user_id)
    path = Path(item.path).with_suffix(".errors.csv")
    if item.status not in {"complete", "failed"} or not path.is_file():
        raise HTTPException(status_code=404, detail="Rejected-row report is unavailable.")
    return FileResponse(path, media_type="text/csv", filename="rejected-leads.csv", headers={"Cache-Control":"private, no-store"})


@router.post("/api/manager/uploads/{upload_id}/retry")
def retry_managed_upload(upload_id: uuid.UUID, principal: Principal = Depends(require_manager), db: Session = Depends(get_db)):
    item = _owned_upload(db, upload_id, principal.user_id)
    db.refresh(item, with_for_update=True)
    if item.status != "failed":
        raise HTTPException(status_code=409, detail="Only failed uploads can be retried.")
    item.status, item.error = "queued", ""
    enqueue_job(db, IMPORT_JOB, payload={"upload_id":str(item.id)})
    db.commit()
    return upload_record(item)


@router.post("/api/manager/uploads", status_code=202)
async def submit_managed_upload(request: Request, principal: Principal = Depends(require_manager), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
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
            await require_manager(principal, settings, db)
            manager = db.get(ManagedAdmin, principal.user_id, populate_existing=True)
            if manager is None or not manager.active:
                raise HTTPException(status_code=403, detail="Managed administration is unavailable.")
            os.replace(partial, path)
            item = LeadUpload(id=upload_id, manager_id=principal.user_id, filename=Path(file.filename).name[:255],
                              state=state, status="queued", path=str(path), sha256=checksum.hexdigest(), bytes=size)
            db.add(item)
            record_activity(db, action="manager_lead_upload_submitted", target_type="lead_upload", target_id=upload_id,
                            actor_id=principal.user_id, reason="CSV submitted for DB2 ingestion",
                            details={"state":state, "bytes":size, "sha256":item.sha256})
            enqueue_job(db, IMPORT_JOB, payload={"upload_id":str(upload_id)})
            db.commit()
            return upload_record(item)
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
