"""Two-person operator recovery for loss of every administrator factor.

Break-glass deliberately constructs ``AuditEntry`` rows directly and commits
them at each recovery checkpoint, rather than going through
``record_activity``. The shared Activity seam refuses to commit so that an
action and its evidence land atomically; this flow must persist authorization
evidence *before* the external MFA provider call, and failure evidence even
when that call raises, so mid-flow commits are the deliberate exception.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from .mfa_provider import MFAProviderError, SupabaseMFAProvider
from .models import AdminMFAState, AuditEntry


@dataclass(frozen=True)
class BreakGlassRequest:
    target_user_id: uuid.UUID
    target_email: str
    operator: str
    authorizer: str
    reason: str
    reference: str

    def validate(self) -> None:
        if not self.operator.strip() or not self.authorizer.strip():
            raise ValueError("operator and authorizer are required")
        if self.operator.strip().casefold() == self.authorizer.strip().casefold():
            raise ValueError("operator and authorizer must be different people")
        if not self.reason.strip():
            raise ValueError("a recovery reason is required")
        if not self.reference.strip():
            raise ValueError("an incident or ticket reference is required")


def _details(request: BreakGlassRequest, *, status: str) -> dict:
    return {
        "recipientEmail": request.target_email.strip().lower(),
        "operator": request.operator.strip(),
        "authorizer": request.authorizer.strip(),
        "reference": request.reference.strip(),
        "status": status,
    }


async def perform_break_glass(
    db: Session,
    provider: SupabaseMFAProvider,
    request: BreakGlassRequest,
) -> dict:
    """Revoke first, then remove factors, preserving evidence across failures."""

    request.validate()
    user = await provider.admin_user(request.target_user_id)
    provider_email = str(user.get("email") or "").strip().lower()
    provider_role = str(
        (user.get("app_metadata") or {}).get("jawnix_role") or ""
    )
    if (
        provider_email != request.target_email.strip().lower()
        or provider_role != "admin"
    ):
        raise ValueError(
            "target identity did not match the confirmed administrator"
        )

    state = db.get(AdminMFAState, request.target_user_id)
    if state is None:
        state = AdminMFAState(
            user_id=request.target_user_id,
            session_generation=1,
        )
        db.add(state)
        db.flush()
    state.session_generation += 1
    db.add(
        AuditEntry(
            action="admin_mfa_break_glass_authorized",
            target_type="administrator_mfa",
            target_id=str(request.target_user_id),
            actor_user_id=request.operator.strip(),
            reason=request.reason.strip(),
            details=_details(request, status="authorized"),
        )
    )
    # Commit revocation and authorization evidence before touching the external
    # provider.  A provider failure cannot resurrect a Jawnix session or erase
    # the fact that the procedure was attempted.
    db.commit()

    try:
        factors = await provider.list_factors(request.target_user_id)
        for factor in factors:
            await provider.delete_factor(request.target_user_id, factor.id)
    except MFAProviderError:
        db.add(
            AuditEntry(
                action="admin_mfa_break_glass_failed",
                target_type="administrator_mfa",
                target_id=str(request.target_user_id),
                actor_user_id=request.operator.strip(),
                reason=request.reason.strip(),
                details=_details(request, status="provider_failed"),
            )
        )
        db.commit()
        raise

    state = db.get(AdminMFAState, request.target_user_id)
    assert state is not None
    state.enrollment_stage = "break_glass_reenrollment_required"
    state.enrollment_baseline_factor_ids = []
    state.enrollment_new_factor_ids = []
    state.active_factor_id = None
    state.replacement_factor_id = None
    state.failed_attempts = 0
    state.failure_window_started_at = None
    state.locked_until = None
    db.add(
        AuditEntry(
            action="admin_mfa_break_glass",
            target_type="administrator_mfa",
            target_id=str(request.target_user_id),
            actor_user_id=request.operator.strip(),
            reason=request.reason.strip(),
            details={
                **_details(request, status="complete"),
                "removedFactorCount": len(factors),
                "accessRestoredTo": "mfa_enrollment_only",
            },
        )
    )
    db.commit()
    return {
        "targetUserId": str(request.target_user_id),
        "targetEmail": provider_email,
        "removedFactorCount": len(factors),
        "sessionGeneration": state.session_generation,
        "accessRestoredTo": "mfa_enrollment_only",
    }
