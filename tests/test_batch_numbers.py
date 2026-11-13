"""Batch numbers: sequential, unique, and immutable."""

from __future__ import annotations

import uuid

import pytest

from jawnix.models import Agent, CustomerProfile, LeadRequest

from conftest import make_request


def test_new_batches_continue_from_the_highest_number(session):
    customer = Agent(slug="northstar", name="Northstar Insurance")
    session.add(customer)
    session.flush()
    first = make_request(session, customer, 10)
    second = make_request(session, customer, 10)
    assert (first.number, second.number) == (1001, 1002)

    # Several Batch Requests flushed together still get distinct numbers.
    batch = []
    for _ in range(3):
        user_id = uuid.uuid4()
        session.add(
            CustomerProfile(
                user_id=user_id,
                email=f"{user_id}@example.com",
                agent=customer,
            )
        )
        batch.append(
            LeadRequest(
                user_id=user_id,
                agent=customer,
                lead_count=5,
                states_snapshot=["TX"],
                delivery_email="owner@example.com",
            )
        )
    session.add_all(batch)
    session.flush()
    assert [item.number for item in batch] == [1003, 1004, 1005]


def test_a_batch_number_never_changes(session):
    customer = Agent(slug="northstar", name="Northstar Insurance")
    session.add(customer)
    session.flush()
    item = make_request(session, customer, 10)
    session.commit()
    item.status = "pending"
    item.number = 2000
    with pytest.raises(ValueError, match="never changes"):
        session.flush()
    session.rollback()
    assert session.get(LeadRequest, item.id).number == 1001


def test_customer_request_contracts_carry_the_batch_number(session):
    from jawnix.customer_requests import request_detail
    from jawnix.schemas import RequestOut

    customer = Agent(slug="northstar", name="Northstar Insurance")
    session.add(customer)
    session.flush()
    item = make_request(session, customer, 10)
    assert request_detail(item).number == item.number == 1001
    assert RequestOut.model_validate(item).number == 1001
