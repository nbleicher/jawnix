from __future__ import annotations

import uuid

import pytest

from jawnix.activity import (
    UnsafeActivityDetailsError,
    record_activity,
)
from jawnix.models import AuditEntry


def test_record_activity_rejects_secret_keys_and_known_material(session):
    known_secret = "known-secret-material-76"

    with pytest.raises(
        UnsafeActivityDetailsError,
        match="secret-bearing key",
    ):
        record_activity(
            session,
            action="user_account_created",
            target_type="user_account",
            target_id=uuid.uuid4(),
            actor_id=uuid.uuid4(),
            reason="Create an account.",
            details={"after": {"accessToken": known_secret}},
        )

    with pytest.raises(
        UnsafeActivityDetailsError,
        match="known secret material",
    ):
        record_activity(
            session,
            action="user_account_created",
            target_type="user_account",
            target_id=uuid.uuid4(),
            actor_id=uuid.uuid4(),
            reason="Create an account.",
            details={"after": {"opaqueValue": known_secret}},
            known_secrets=(known_secret,),
        )

    assert session.query(AuditEntry).count() == 0


def test_record_activity_failure_is_not_silenced(session):
    with pytest.raises(ValueError, match="JSON-safe"):
        record_activity(
            session,
            action="customer_updated",
            target_type="customer",
            target_id=1,
            actor_id=uuid.uuid4(),
            reason="Change Customer membership.",
            details={"after": {"unsupported": object()}},
        )

    assert session.query(AuditEntry).count() == 0
