from __future__ import annotations

import uuid
from pathlib import Path
from fastapi import (
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
)
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..activity import record_activity
from ..auth import Principal, require_admin, require_principal
from ..config import Settings, get_settings
from ..database import get_db
from ..exclusions import (
    EXCLUSION_TYPES,
    ExclusionDecisionError,
    decide_exclusion_list,
    exclusion_list_status,
)
from ..inventory import exclusion_lists_overview
from ..jobs import enqueue_job
from ..models import Customer, ExclusionList
from ..schemas import ActionReason
from ._shared import UploadTooLargeError, _mapped_customer_profile, _store_exclusion_upload
from fastapi import APIRouter


router = APIRouter()


@router.post("/api/me/exclusion-lists", status_code=202)
def upload_customer_exclusion_list(
    file: UploadFile = File(...),
    exclusion_type: str = Form(..., alias="type"),
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    profile = _mapped_customer_profile(db, principal)
    normalized_type = exclusion_type.strip().casefold()
    if normalized_type not in EXCLUSION_TYPES:
        raise HTTPException(status_code=422, detail="Unknown Exclusion List type.")
    if not file.filename or not file.filename.casefold().endswith(".csv"):
        raise HTTPException(status_code=422, detail="Upload a CSV file.")
    item = ExclusionList(
        customer_id=profile.customer_id,
        uploaded_by=str(principal.user_id),
        exclusion_type=normalized_type,
        filename=Path(file.filename).name,
        storage_path="",
    )
    db.add(item)
    db.flush()
    try:
        item.storage_path = str(
            _store_exclusion_upload(
                file, exclusion_list_id=item.id, settings=settings
            )
        )
    except UploadTooLargeError:
        db.rollback()
        raise HTTPException(
            status_code=413,
            detail="CSV uploads must be 10 MB or smaller.",
        ) from None
    except OSError as exc:
        db.rollback()
        raise HTTPException(
            status_code=503,
            detail="The Exclusion List could not be stored.",
        ) from exc
    enqueue_job(
        db,
        "ingest_exclusion_list",
        payload={"exclusion_list_id": str(item.id)},
    )
    record_activity(
        db,
        action="exclusion_list_uploaded",
        target_type="exclusion_list",
        target_id=item.id,
        actor_id=principal.user_id,
        reason="Customer uploaded an Exclusion List",
        details={
            "customerId": profile.customer_id,
            "type": item.exclusion_type,
            "filename": item.filename,
            "global": False,
        },
    )
    db.commit()
    return exclusion_list_status(item, db)


@router.get("/api/me/exclusion-lists/{exclusion_list_id}")
def customer_exclusion_list_status(
    exclusion_list_id: uuid.UUID,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
):
    profile = _mapped_customer_profile(db, principal)
    item = db.scalar(
        select(ExclusionList).where(
            ExclusionList.id == exclusion_list_id,
            ExclusionList.customer_id == profile.customer_id,
        )
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Exclusion List was not found.")
    return exclusion_list_status(item, db)


@router.get("/api/me/exclusion-lists")
def list_customer_exclusion_lists(
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
):
    profile = _mapped_customer_profile(db, principal)
    return [
        exclusion_list_status(item, db)
        for item in db.scalars(
            select(ExclusionList)
            .where(ExclusionList.customer_id == profile.customer_id)
            .order_by(ExclusionList.created_at.desc(), ExclusionList.id)
        )
    ]


@router.post("/api/admin/exclusion-lists", status_code=202)
def upload_admin_exclusion_list(
    file: UploadFile = File(...),
    exclusion_type: str = Form(..., alias="type"),
    reason: str = Form(...),
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    normalized_type = exclusion_type.strip().casefold()
    normalized_reason = reason.strip()
    if normalized_type not in EXCLUSION_TYPES:
        raise HTTPException(status_code=422, detail="Unknown Exclusion List type.")
    if not normalized_reason:
        raise HTTPException(status_code=422, detail="An upload reason is required.")
    if not file.filename or not file.filename.casefold().endswith(".csv"):
        raise HTTPException(status_code=422, detail="Upload a CSV file.")
    item = ExclusionList(
        customer_id=None,
        uploaded_by=str(principal.user_id),
        exclusion_type=normalized_type,
        filename=Path(file.filename).name,
        storage_path="",
        decision_reason=normalized_reason,
    )
    db.add(item)
    db.flush()
    try:
        item.storage_path = str(
            _store_exclusion_upload(
                file, exclusion_list_id=item.id, settings=settings
            )
        )
    except UploadTooLargeError:
        db.rollback()
        raise HTTPException(
            status_code=413,
            detail="CSV uploads must be 10 MB or smaller.",
        ) from None
    except OSError as exc:
        db.rollback()
        raise HTTPException(
            status_code=503,
            detail="The Exclusion List could not be stored.",
        ) from exc
    enqueue_job(
        db,
        "ingest_exclusion_list",
        payload={"exclusion_list_id": str(item.id)},
    )
    record_activity(
        db,
        action="exclusion_list_uploaded",
        target_type="exclusion_list",
        target_id=item.id,
        actor_id=principal.user_id,
        reason=normalized_reason,
        details={
            "customerId": None,
            "type": item.exclusion_type,
            "filename": item.filename,
            "globalOnIngestion": True,
        },
    )
    db.commit()
    return exclusion_list_status(item, db)


@router.get("/api/admin/exclusion-lists")
def admin_exclusion_lists(
    page: int = Query(default=1, ge=1),
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Lists waiting for a global decision, then every list, newest first."""
    return exclusion_lists_overview(db, page=page)


@router.get("/api/admin/exclusion-lists/{exclusion_list_id}")
def admin_exclusion_list_status(
    exclusion_list_id: uuid.UUID,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    item = db.get(ExclusionList, exclusion_list_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Exclusion List was not found.")
    return exclusion_list_status(item, db)


@router.get("/api/admin/customers/{customer_id}/exclusion-lists")
def admin_customer_exclusion_lists(
    customer_id: int,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    customer = db.get(Customer, customer_id)
    if customer is None or customer.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Customer was not found.")
    return [
        exclusion_list_status(item, db)
        for item in db.scalars(
            select(ExclusionList)
            .where(ExclusionList.customer_id == customer_id)
            .order_by(ExclusionList.created_at.desc(), ExclusionList.id)
        )
    ]


@router.post("/api/admin/exclusion-lists/{exclusion_list_id}/{action}")
def admin_decide_exclusion_list(
    exclusion_list_id: uuid.UUID,
    action: str,
    payload: ActionReason,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    try:
        item = decide_exclusion_list(
            db,
            exclusion_list_id,
            action,
            actor_id=principal.user_id,
            reason=payload.reason,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except ExclusionDecisionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    db.commit()
    return exclusion_list_status(item, db)
