from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from jawnix.feedback import (
    apply_disposition_controls,
    release_report_hold,
)
from jawnix.allocation import inventory_count
from jawnix.config import Settings
from jawnix.models import (
    Agent,
    DistributionEvent,
    EligibilityHold,
    Lead,
    LeadDispositionTransition,
    LeadReport,
    CustomerProfile,
    LeadRequest,
    RequestStatus,
)


def _controls(session, disposition: str):
    customer = Agent(slug=f"customer-{disposition}", name="Customer")
    lead = Lead(
        phone=f"215555{session.query(Lead).count() + 1:04d}",
        title="Business",
        state="PA",
    )
    session.add_all([customer, lead])
    session.flush()
    event = DistributionEvent(
        lead_id=lead.id,
        customer_id=customer.id,
        customer_name=customer.name,
        phone=lead.phone,
        title=lead.title,
        state=lead.state,
    )
    session.add(event)
    session.flush()
    transition = LeadDispositionTransition(
        distribution_event_id=event.id,
        customer_id=customer.id,
        actor_user_id=uuid.uuid4(),
        disposition=disposition,
        note="Customer evidence",
        created_at=datetime(2026, 7, 27, 12, tzinfo=timezone.utc),
    )
    session.add(transition)
    session.flush()
    return event, transition


@pytest.mark.parametrize(
    ("disposition", "reason", "held"),
    [
        ("invalid_phone", "invalid_phone", True),
        ("wrong_business", "wrong_business_or_title", False),
        ("do_not_contact", "do_not_contact_or_legal", True),
    ],
)
def test_report_and_hold_policy(session, disposition, reason, held):
    event, transition = _controls(session, disposition)
    report, hold = apply_disposition_controls(
        session, event, transition
    )
    assert report.reason == reason
    assert (hold is not None) is held

    same_report, same_hold = apply_disposition_controls(
        session, event, transition
    )
    assert same_report.id == report.id
    assert (same_hold.id if same_hold else None) == (
        hold.id if hold else None
    )
    assert session.scalar(select(func.count(LeadReport.id))) == 1


def test_customer_correction_does_not_release_hold(session):
    event, transition = _controls(session, "invalid_phone")
    _, hold = apply_disposition_controls(session, event, transition)
    correction = LeadDispositionTransition(
        distribution_event_id=event.id,
        customer_id=transition.customer_id,
        actor_user_id=uuid.uuid4(),
        disposition="positive_response",
        previous_transition_id=transition.id,
    )
    session.add(correction)
    session.flush()
    assert session.get(EligibilityHold, hold.id).active is True


def test_admin_resolution_releases_hold_idempotently(session):
    event, transition = _controls(session, "do_not_contact")
    report, hold = apply_disposition_controls(session, event, transition)
    released = release_report_hold(
        session,
        report,
        actor_id="admin",
        reason="Reviewed",
    )
    assert released.id == hold.id
    assert released.active is False
    assert release_report_hold(
        session,
        report,
        actor_id="admin",
        reason="Replay",
    ) is None


def test_active_hold_blocks_allocation_until_admin_release(session):
    event, transition = _controls(session, "invalid_phone")
    report, _ = apply_disposition_controls(session, event, transition)
    event_customer = Agent(
        slug="held-allocation-customer",
        name="Held Allocation Customer",
    )
    session.add(event_customer)
    profile = CustomerProfile(
        user_id=uuid.uuid4(),
        email="held@example.com",
        licensed_states=["PA"],
        agent=event_customer,
    )
    request = LeadRequest(
        user_id=profile.user_id,
        agent=event_customer,
        lead_count=1,
        states_snapshot=["PA"],
        state_mode="all_saved",
        delivery_email=profile.email,
        status=RequestStatus.approved.value,
    )
    session.add_all([profile, request])
    session.flush()
    assert inventory_count(session, request, Settings()) == 0
    release_report_hold(
        session,
        report,
        actor_id="admin",
        reason="Reviewed",
    )
    assert inventory_count(session, request, Settings()) == 1
