from __future__ import annotations

import io
import uuid
from pathlib import Path
from fastapi import (
    Depends,
    File,
    HTTPException,
    Response,
    UploadFile,
)
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from ..activity import record_activity
from ..auth import Principal, require_admin
from ..config import Settings, get_settings
from ..database import get_db
from ..inventory import inventory_overview, usable_by_state_csv
from ..jobs import enqueue_job
from ..niche_policy import (
    unmapped_inventory_query,
    upload_status as niche_assignment_upload_status,
)
from ..models import InventoryConflict, NicheAssignmentUpload
from ..schemas import ActionReason
from ..fulfillment import describe_conflict
from ._shared import UploadTooLargeError, _store_bounded_upload
from fastapi import APIRouter


router = APIRouter()


@router.get("/api/admin/inventory-conflicts/{conflict_id}")
def admin_inventory_conflict_detail(
    conflict_id: uuid.UUID,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    conflict = db.get(InventoryConflict, conflict_id)
    if conflict is None:
        raise HTTPException(
            status_code=404, detail="Inventory Conflict was not found."
        )
    return describe_conflict(db, conflict)


@router.get("/api/admin/pool-breakdown")
def get_pool_breakdown(
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    from ..pool_analytics import read_pool_breakdown

    return read_pool_breakdown(db)


@router.post("/api/admin/pool-breakdown/refresh")
def refresh_pool_breakdown_endpoint(
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Queue the background recount; the response is the current snapshot.

    The breakdown spans all of DB1, so it is never counted in a request.
    """
    from ..inventory_snapshots import enqueue_inventory_refresh
    from ..pool_analytics import read_pool_breakdown

    job = enqueue_inventory_refresh(db, reason="manual")
    db.commit()
    return {**read_pool_breakdown(db), "refreshQueued": True, "jobId": job.id}


@router.get("/api/admin/inventory-snapshots")
def get_inventory_snapshots(
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Latest usable DB1 and DB2 totals with their as-of times."""
    from ..inventory_snapshots import read_inventory_snapshots

    return read_inventory_snapshots(db)


@router.post("/api/admin/inventory-snapshots/refresh", status_code=202)
def refresh_inventory_snapshots_endpoint(
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Queue one background recount, reusing a refresh not yet started."""
    from ..inventory_snapshots import (
        enqueue_inventory_refresh,
        read_inventory_snapshots,
    )

    enqueue_inventory_refresh(db, reason="manual")
    db.commit()
    return read_inventory_snapshots(db)


@router.get("/api/admin/inventory")
def admin_inventory(
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """The Inventory page: usable DB1 and DB2 snapshots and the tools beside them."""
    return inventory_overview(db)


@router.get("/api/admin/inventory/usable-by-state.csv")
def admin_usable_by_state_csv(
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Usable Leads per state in DB1 and DB2, from the stored snapshots."""
    return Response(
        content=usable_by_state_csv(db),
        media_type="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="usable-by-state.csv"',
            "Cache-Control": "private, no-store",
        },
    )


def _store_niche_assignment_upload(
    upload: UploadFile, *, upload_id: uuid.UUID, settings: Settings
) -> Path:
    directory = Path(settings.batch_dir) / "niche-assignments"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{upload_id}.csv"
    _store_bounded_upload(upload, path)
    return path


@router.get("/api/admin/niche-assignments/export")
def export_unmapped_niche_inventory(
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    output = io.StringIO()
    output.write("phone,title,state\r\n")
    for lead in db.scalars(unmapped_inventory_query()):
        output.write(f'"{lead.phone}","{lead.title.replace(chr(34), chr(34) * 2)}","{lead.state}"\r\n')
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="unmapped-inventory.csv"'},
    )


@router.post("/api/admin/niche-assignments", status_code=202)
def upload_niche_assignments(
    file: UploadFile = File(...),
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    if not file.filename or not file.filename.casefold().endswith(".csv"):
        raise HTTPException(status_code=422, detail="Upload a CSV file.")
    item = NicheAssignmentUpload(
        uploaded_by=str(principal.user_id),
        filename=Path(file.filename).name,
        storage_path="",
    )
    db.add(item)
    db.flush()
    try:
        item.storage_path = str(
            _store_niche_assignment_upload(file, upload_id=item.id, settings=settings)
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
            status_code=503, detail="The Niche Assignment CSV could not be stored."
        ) from exc
    enqueue_job(
        db, "ingest_niche_assignments", payload={"niche_assignment_upload_id": str(item.id)}
    )
    record_activity(
        db,
        action="niche_assignment_uploaded",
        target_type="niche_assignment_upload",
        target_id=item.id,
        actor_id=principal.user_id,
        reason="Administrator uploaded Niche Assignments",
        details={"filename": item.filename},
    )
    db.commit()
    return niche_assignment_upload_status(item)


@router.get("/api/admin/niche-assignments/{upload_id}")
def niche_assignment_status(
    upload_id: uuid.UUID,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    item = db.get(NicheAssignmentUpload, upload_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Niche Assignment upload was not found.")
    return niche_assignment_upload_status(item)


@router.post("/api/admin/inventory-conflicts/{conflict_id}/{action}")
def admin_inventory_conflict_action(
    conflict_id: uuid.UUID,
    action: str,
    payload: ActionReason,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    from ..allocation import decide_inventory_conflict

    try:
        result = decide_inventory_conflict(
            db,
            conflict_id,
            action,
            str(principal.user_id),
            payload.reason,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    db.commit()
    return result
