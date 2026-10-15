"""Registration and history restoration races against disposable PostgreSQL."""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from jawnix.customer_accounts import UserAccountConflict
from jawnix.customer_registration import complete_registration, decide_registration, lock_registration_identity, set_registration_states
from jawnix.database import Base
from jawnix.models import Customer, CustomerHistoryIdentity, CustomerRegistration, UserAccount, utcnow
from jawnix.schemas import RegistrationDecision, SignupProfile

DATABASE_URL = os.environ.get("JAWNIX_ACCEPTANCE_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="requires disposable PostgreSQL")


@pytest.fixture
def registration_engine():
    engine = create_engine(DATABASE_URL)
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


def test_concurrent_verified_callbacks_create_one_customer(registration_engine):
    user_id = uuid.uuid4()
    details = SignupProfile(first_name="New", last_name=user_id.hex, phone="6155550100", licensed_states=["TN"])
    ready = Barrier(2)
    def finish(_):
        with Session(registration_engine) as db:
            ready.wait(timeout=5)
            lock_registration_identity(db, user_id)
            registration = complete_registration(db, user_id=user_id, email=f"{user_id}@example.test", details=details)
            customer_id = registration.customer_id
            db.commit()
            return customer_id
    with ThreadPoolExecutor(max_workers=2) as executor:
        customer_ids = list(executor.map(finish, range(2)))
    assert customer_ids[0] == customer_ids[1] and customer_ids[0] is not None
    with Session(registration_engine) as db:
        assert db.get(UserAccount, user_id).active
        assert db.scalar(select(func.count()).select_from(CustomerRegistration).where(CustomerRegistration.auth_user_id == user_id)) == 1


def test_two_registrations_cannot_restore_the_same_history(registration_engine):
    last_name = uuid.uuid4().hex
    details = SignupProfile(first_name="Archived", last_name=last_name, phone="6155550100", licensed_states=["TN"])
    users = [uuid.uuid4(), uuid.uuid4()]
    with Session(registration_engine) as db:
        customer = Customer(slug=last_name, name=f"Archived {last_name}", active=False, deleted_at=utcnow())
        db.add(customer); db.flush()
        customer_id = customer.id
        db.add(CustomerHistoryIdentity(customer_id=customer.id, first_name="Archived", last_name=last_name)); db.commit()
        for user_id in users:
            complete_registration(db, user_id=user_id, email=f"{user_id}@example.test", details=details)
        db.commit()
    ready = Barrier(2)
    def approve(user_id):
        with Session(registration_engine) as db:
            ready.wait(timeout=5)
            try:
                result = decide_registration(db, user_id, RegistrationDecision(decision="restore", restore_customer_id=customer_id), "operator")
                db.commit()
                return result.status
            except UserAccountConflict:
                db.rollback()
                return "refused"
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(approve, users)) == ["active", "refused"]
    with Session(registration_engine) as db:
        assert db.scalar(select(func.count()).select_from(UserAccount).where(UserAccount.customer_id == customer_id, UserAccount.active.is_(True))) == 1
        assert sorted(db.get(CustomerRegistration, user_id).status for user_id in users) == ["active", "awaiting_review"]


def test_concurrent_state_setup_activates_one_customer_without_overwriting_states(registration_engine):
    user_id = uuid.uuid4()
    with Session(registration_engine) as db:
        complete_registration(db, user_id=user_id, email=f"{user_id}@example.test",
                              details=SignupProfile(first_name="New", last_name=user_id.hex, phone="6155550100"))
        db.commit()
    ready = Barrier(2)
    def finish(states):
        with Session(registration_engine) as db:
            ready.wait(timeout=5)
            registration = set_registration_states(db, user_id, states)
            result = registration.customer_id, registration.licensed_states
            db.commit()
            return result
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(finish, [["TN"], ["SC"]]))
    assert results[0] == results[1]
    assert results[0][0] is not None and results[0][1] in [["TN"], ["SC"]]
    with Session(registration_engine) as db:
        assert db.get(UserAccount, user_id).active
        assert db.get(Customer, results[0][0]).licensed_states == results[0][1]
