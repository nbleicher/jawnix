from __future__ import annotations

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.orm import Session
from ..config import Settings, get_settings
from ..database import get_db
from fastapi import APIRouter


router = APIRouter()


@router.get("/api/healthz")
def healthz(settings: Settings = Depends(get_settings)):
    return {"ok": True, "billingEnabled": settings.billing_enabled}


@router.get("/api/readyz")
def readyz(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"ok": True}
