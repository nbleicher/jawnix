from __future__ import annotations

import io
import uuid
from datetime import date
from fastapi import Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..activity import record_activity
from ..auth import Principal, require_admin
from ..config import Settings, get_settings
from ..database import get_db
from ..recommendations import RecommendationDecisionError, decide_recommendation
from ..models import (
    DailySourcePerformance,
    PerformanceSuggestionNote,
    SourceNicheMapping,
    utcnow,
)
from ..schemas import ActionReason, RecommendationDecision, SourceNicheDecision
from fastapi import APIRouter


router = APIRouter()


def _performance_rows(
    db: Session,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    state: str = "",
    keyword: str = "",
    niche: str = "",
    confidence: str = "",
    action_state: str = "",
    latest: bool = True,
) -> list[DailySourcePerformance]:
    query = select(DailySourcePerformance)
    if start_date is not None:
        query = query.where(
            DailySourcePerformance.snapshot_date >= start_date
        )
    if end_date is not None:
        query = query.where(
            DailySourcePerformance.snapshot_date <= end_date
        )
    if state:
        query = query.where(
            DailySourcePerformance.state == state.strip().upper()
        )
    if keyword:
        query = query.where(
            DailySourcePerformance.keyword.ilike(
                f"%{keyword.strip()}%"
            )
        )
    if niche:
        query = query.where(
            DailySourcePerformance.niche == niche.strip()
        )
    if confidence:
        query = query.where(
            DailySourcePerformance.eligibility == confidence.strip()
        )
    if action_state:
        query = query.where(
            DailySourcePerformance.action_state
            == action_state.strip()
        )
    rows = list(
        db.scalars(
            query.order_by(
                DailySourcePerformance.snapshot_date.desc(),
                DailySourcePerformance.segment_key,
            )
        )
    )
    if not latest:
        return rows
    result: list[DailySourcePerformance] = []
    seen: set[str] = set()
    for row in rows:
        if row.segment_key in seen:
            continue
        seen.add(row.segment_key)
        result.append(row)
    return result


def _performance_response(item: DailySourcePerformance) -> dict:
    return {
        "id": str(item.id),
        "date": item.snapshot_date.isoformat(),
        "segment": item.segment_key,
        "state": item.state,
        "keyword": item.keyword,
        "niche": item.niche,
        "nicheConfirmed": item.niche_confirmed,
        "counts": item.counts,
        "rates": item.rates,
        "intervals": item.intervals,
        "trend": item.trend,
        "confidence": item.eligibility,
        "actionState": item.action_state,
        "evidenceChecksum": item.evidence_checksum,
    }


@router.get("/api/admin/source-performance")
def source_performance(
    start_date: date | None = None,
    end_date: date | None = None,
    state: str = "",
    keyword: str = "",
    niche: str = "",
    confidence: str = "",
    action_state: str = "",
    latest: bool = True,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    rows = _performance_rows(
        db,
        start_date=start_date,
        end_date=end_date,
        state=state,
        keyword=keyword,
        niche=niche,
        confidence=confidence,
        action_state=action_state,
        latest=latest,
    )
    from ..performance import source_performance_summary

    return {
        **source_performance_summary(db),
        "rows": [_performance_response(item) for item in rows],
    }


@router.get("/api/admin/source-performance.csv")
def export_source_performance(
    start_date: date | None = None,
    end_date: date | None = None,
    state: str = "",
    keyword: str = "",
    niche: str = "",
    confidence: str = "",
    action_state: str = "",
    latest: bool = True,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    import csv
    import io

    rows = _performance_rows(
        db,
        start_date=start_date,
        end_date=end_date,
        state=state,
        keyword=keyword,
        niche=niche,
        confidence=confidence,
        action_state=action_state,
        latest=latest,
    )
    stream = io.StringIO()
    columns = [
        "date",
        "state",
        "keyword",
        "niche",
        "niche_confirmed",
        "delivered",
        "worked",
        "rated",
        "positive",
        "booked",
        "canceled",
        "no_show",
        "invalid",
        "wrong_business",
        "do_not_contact",
        "positive_rate",
        "booked_rate",
        "good_rate",
        "poor_rate",
        "confidence",
        "action_state",
    ]
    writer = csv.DictWriter(stream, fieldnames=columns)
    writer.writeheader()
    for item in rows:
        writer.writerow(
            {
                "date": item.snapshot_date.isoformat(),
                "state": item.state,
                "keyword": item.keyword,
                "niche": item.niche,
                "niche_confirmed": item.niche_confirmed,
                **item.counts,
                "positive_rate": item.rates.get(
                    "positiveResponse", 0
                ),
                "booked_rate": item.rates.get(
                    "appointmentBooked", 0
                ),
                "good_rate": item.rates.get("good", 0),
                "poor_rate": item.rates.get("poor", 0),
                "confidence": item.eligibility,
                "action_state": item.action_state,
            }
        )
    return Response(
        content=stream.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": (
                'attachment; filename="keyword-performance.csv"'
            )
        },
    )


@router.get("/api/admin/source-performance/{segment_key}/history")
def source_performance_history(
    segment_key: str,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    snapshots = list(
        db.scalars(
            select(DailySourcePerformance)
            .where(
                DailySourcePerformance.segment_key == segment_key
            )
            .order_by(DailySourcePerformance.snapshot_date.desc())
        )
    )
    notes = {
        item.snapshot_id: item
        for item in db.scalars(
            select(PerformanceSuggestionNote).where(
                PerformanceSuggestionNote.snapshot_id.in_(
                    [snapshot.id for snapshot in snapshots]
                )
            )
        )
    }
    return {
        "rows": [
            {
                **_performance_response(item),
                "suggestionNote": (
                    notes[item.id].text if item.id in notes else None
                ),
            }
            for item in snapshots
        ]
    }


@router.post("/api/admin/source-niches/{segment_key}/confirm")
def confirm_source_niche(
    segment_key: str,
    payload: SourceNicheDecision,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    mapping = db.scalar(
        select(SourceNicheMapping)
        .where(SourceNicheMapping.segment_key == segment_key)
        .with_for_update()
    )
    if mapping is None:
        raise HTTPException(
            status_code=404,
            detail="Source Segment Niche mapping was not found.",
        )
    previous = {
        "niche": mapping.niche,
        "confirmed": mapping.confirmed,
    }
    next_niche = payload.niche.strip()
    if mapping.confirmed and mapping.niche == next_niche:
        return {
            "segment": mapping.segment_key,
            "niche": mapping.niche,
            "confirmed": True,
        }
    mapping.niche = next_niche
    mapping.confirmed = True
    mapping.denied_by = ""
    mapping.denied_at = None
    mapping.confirmed_by = str(principal.user_id)
    mapping.confirmed_at = utcnow()
    mapping.updated_at = mapping.confirmed_at
    record_activity(
        db,
        action="source_niche_confirmed",
        target_type="source_segment",
        target_id=mapping.segment_key,
        actor_id=principal.user_id,
        reason=payload.reason,
        details={
            "before": previous,
            "after": {
                "niche": mapping.niche,
                "confirmed": mapping.confirmed,
            },
        },
    )
    db.commit()
    return {
        "segment": mapping.segment_key,
        "niche": mapping.niche,
        "confirmed": True,
    }


@router.post("/api/admin/source-niches/{segment_key}/deny")
def deny_source_niche(
    segment_key: str,
    payload: ActionReason,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    mapping = db.scalar(
        select(SourceNicheMapping)
        .where(SourceNicheMapping.segment_key == segment_key)
        .with_for_update()
    )
    if mapping is None:
        raise HTTPException(
            status_code=404,
            detail="Source Segment Niche mapping was not found.",
        )
    if mapping.confirmed:
        raise HTTPException(
            status_code=409,
            detail="A confirmed Source Niche mapping cannot be denied.",
        )
    if mapping.denied_at is not None:
        return {
            "segment": mapping.segment_key,
            "niche": mapping.niche,
            "confirmed": False,
            "denied": True,
        }
    mapping.denied_by = str(principal.user_id)
    mapping.denied_at = utcnow()
    mapping.updated_at = mapping.denied_at
    record_activity(
        db,
        action="source_niche_denied",
        target_type="source_segment",
        target_id=mapping.segment_key,
        actor_id=principal.user_id,
        reason=payload.reason,
        details={
            "niche": mapping.niche,
            "proposalSource": mapping.proposal_source,
            "confirmed": False,
        },
    )
    db.commit()
    return {
        "segment": mapping.segment_key,
        "niche": mapping.niche,
        "confirmed": False,
        "denied": True,
    }


@router.get("/api/admin/acquisition")
def admin_acquisition(
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """One authenticated read for the Acquisition workspace.

    Read-only by design: every action the workspace offers posts back to the
    endpoint that already owns that decision, so this contract can never
    become a second way to change acquisition.
    """
    from ..acquisition import workspace as acquisition_workspace

    return acquisition_workspace(db)


@router.post(
    "/api/admin/source-recommendations/{recommendation_id}/{action}"
)
def decide_source_recommendation(
    recommendation_id: uuid.UUID,
    action: str,
    payload: RecommendationDecision,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    if action not in {"approve", "deny"}:
        raise HTTPException(status_code=404, detail="Unknown action.")
    # A caller that showed the evidence decides on the evidence it showed. The
    # check happens inside decide_recommendation, under the same row lock the
    # decision takes, so it cannot be raced. A caller that showed nothing sends
    # nothing and stays unbound, which is how the legacy page still works.
    try:
        recommendation = decide_recommendation(
            db,
            recommendation_id,
            action,
            actor_id=str(principal.user_id),
            reason=payload.reason.strip(),
            apply_enabled=settings.recommendation_apply_enabled,
            shadow_mode=settings.recommendation_shadow_mode,
            expected_evidence_checksum=payload.evidence_checksum,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except RecommendationDecisionError as exc:
        db.commit()
        raise HTTPException(status_code=409, detail=str(exc)) from None
    db.commit()
    return {
        "id": str(recommendation.id),
        "status": recommendation.status,
        "resultingConfigurationId": (
            str(recommendation.resulting_configuration_id)
            if recommendation.resulting_configuration_id is not None
            else None
        ),
    }
