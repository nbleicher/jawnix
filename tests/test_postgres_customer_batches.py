"""Batches and the new-copy ask against disposable PostgreSQL."""

import os
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from jawnix.batch_copies import NEW_COPY_JOB, NEW_COPY_LIMIT, NewCopyRefused, request_new_copy
from jawnix.customer_history import request_history_scope
from jawnix.customer_requests import customer_batches, find_customer_batch
from jawnix.database import Base
from jawnix.models import (
    Agent,
    AuditEntry,
    BatchArtifact,
    CustomerProfile,
    Job,
    LeadRequest,
    RequestStatus,
)

DATABASE_URL = os.environ.get("JAWNIX_ACCEPTANCE_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="requires disposable PostgreSQL")


@pytest.fixture
def engine():
    value = create_engine(DATABASE_URL)
    Base.metadata.create_all(value)
    try:
        yield value
    finally:
        value.dispose()


def _customer_with_batches(db: Session, tmp_path, count: int):
    """A Customer with ``count`` delivered batches whose files expired.

    The batches hold no leads, so their (empty) Distribution Events are whole
    and a copy can be asked for without allocating inventory.
    """

    user_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    customer = Agent(slug=f"pg-batches-{user_id}", name="Northstar Insurance", licensed_states=["FL"])
    profile = CustomerProfile(
        user_id=user_id,
        email=f"{user_id}@example.test",
        licensed_states=["FL"],
        agent=customer,
        mapping_confirmed_at=now,
    )
    db.add_all([customer, profile])
    db.flush()
    items = []
    for index in range(count):
        item = LeadRequest(
            user_id=user_id,
            agent=customer,
            lead_count=0,
            rows_per_file=1,
            states_snapshot=["FL"],
            state_mode="all_saved",
            delivery_email=profile.email,
            status=RequestStatus.delivered.value,
            created_at=now - timedelta(days=40 + index),
            delivered_at=now - timedelta(days=39 + index),
        )
        db.add(item)
        db.flush()
        db.add(
            BatchArtifact(
                request_id=item.id,
                path=str(tmp_path / f"{item.id}.zip"),
                filename="batch.zip",
                row_count=0,
                parts=[],
                byte_count=0,
                sha256="a" * 64,
                expires_at=now - timedelta(days=9 + index),
            )
        )
        items.append(item)
    db.commit()
    return user_id, profile, items


def test_batches_read_by_number_and_uuid_with_stages(engine, tmp_path):
    with Session(engine, expire_on_commit=False) as db:
        user_id, profile, items = _customer_with_batches(db, tmp_path, 2)
        scope = request_history_scope(profile, user_id)

        by_number = find_customer_batch(db, str(items[0].number), scope)
        by_id = find_customer_batch(db, str(items[1].id), scope)
        details = customer_batches(db, items)

        assert by_number is not None and by_number.id == items[0].id
        assert by_id is not None and by_id.id == items[1].id
        assert find_customer_batch(db, "999999999", scope) is None
        assert [detail.stage for detail in details] == ["file_expired", "file_expired"]
        assert all(detail.can_request_copy for detail in details)


def test_two_asks_for_one_batch_queue_one_copy(engine, tmp_path):
    with Session(engine, expire_on_commit=False) as setup:
        user_id, profile, items = _customer_with_batches(db=setup, tmp_path=tmp_path, count=1)
    batch_id = items[0].id
    scope_user = user_id

    first = Session(engine, expire_on_commit=False)
    second_result: list[object] = []
    try:
        profile_first = first.get(CustomerProfile, scope_user)
        locked = find_customer_batch(
            first, str(batch_id), request_history_scope(profile_first, scope_user), for_update=True
        )
        _, created_first = request_new_copy(first, item=locked, actor_id=scope_user)

        def ask_again():
            with Session(engine, expire_on_commit=False) as second:
                profile_second = second.get(CustomerProfile, scope_user)
                # Blocks on the row lock until the first ask commits.
                item = find_customer_batch(
                    second,
                    str(batch_id),
                    request_history_scope(profile_second, scope_user),
                    for_update=True,
                )
                second_result.append(request_new_copy(second, item=item, actor_id=scope_user)[1])
                second.commit()

        thread = threading.Thread(target=ask_again)
        thread.start()
        thread.join(timeout=0.5)
        assert thread.is_alive(), "the second ask must wait for the first one's row lock"
        first.commit()
        thread.join(timeout=10)
        assert not thread.is_alive()
    finally:
        first.close()

    assert created_first is True
    assert second_result == [False]
    with Session(engine) as db:
        jobs = db.scalar(
            select(func.count()).select_from(Job).where(Job.kind == NEW_COPY_JOB, Job.request_id == batch_id)
        )
        audits = db.scalar(
            select(func.count()).select_from(AuditEntry).where(
                AuditEntry.action == "batch_artifact_copy_requested",
                AuditEntry.target_id == str(batch_id),
            )
        )
    assert jobs == 1
    assert audits == 1


def test_copies_are_limited_per_customer_in_a_day(engine, tmp_path):
    with Session(engine, expire_on_commit=False) as db:
        user_id, profile, items = _customer_with_batches(db, tmp_path, NEW_COPY_LIMIT + 1)
        scope = request_history_scope(profile, user_id)
        for item in items[:NEW_COPY_LIMIT]:
            locked = find_customer_batch(db, str(item.id), scope, for_update=True)
            request_new_copy(db, item=locked, actor_id=user_id)
            db.commit()

        locked = find_customer_batch(db, str(items[-1].id), scope, for_update=True)
        with pytest.raises(NewCopyRefused) as refused:
            request_new_copy(db, item=locked, actor_id=user_id)
        db.rollback()

    assert refused.value.status_code == 429
