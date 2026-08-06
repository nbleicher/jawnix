"""The administrator Fulfillment read and action contracts (#57).

The workspace has to answer three questions from one place: what Batch Requests
need attention, which Inventory Conflicts are awaiting a decision, and what
delivery failed. Every action it offers is projected from jawnix/fulfillment.py
so the screen cannot invent one the domain refuses.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from jawnix.allocation import allocate_request
from jawnix.api import app
from jawnix.auth import Principal, require_admin, require_principal
from jawnix.database import get_db
from jawnix.models import (
    Agency,
    Agent,
    AuditEntry,
    BatchArtifact,
    CustomerProfile,
    InventoryConflict,
    Lead,
    LeadRequest,
    RequestStatus,
)

ADMIN_ID = uuid.uuid4()


def as_admin(session):
    """Bind the app to this test's session and an authenticated administrator."""

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=ADMIN_ID,
        email="admin@example.com",
        role="admin",
        csrf="test",
    )
    app.dependency_overrides[require_principal] = lambda: Principal(
        user_id=ADMIN_ID,
        email="admin@example.com",
        role="admin",
        csrf="test",
    )
    return TestClient(app)


def customer_with_request(
    session,
    count: int = 1,
    states: list[str] | None = None,
    status: str = RequestStatus.pending.value,
):
    """A Customer in an Agency with one Batch Request in a chosen state."""
    suffix = uuid.uuid4().hex[:8]
    user_id = uuid.uuid4()
    scope = states or ["TX"]
    agency = Agency(slug=f"agency-{suffix}", name="Northstar Agency")
    customer = Agent(slug=f"customer-{suffix}", name="Reported Customer", agency=agency)
    profile = CustomerProfile(
        user_id=user_id,
        email=f"customer-{suffix}@example.com",
        licensed_states=scope,
        agent=customer,
        mapping_confirmed_at=datetime.now(timezone.utc),
    )
    request = LeadRequest(
        user_id=user_id,
        agent=customer,
        lead_count=count,
        states_snapshot=sorted(scope),
        state_mode="all_saved",
        delivery_email=profile.email,
        status=status,
    )
    session.add_all([agency, customer, profile, request])
    session.flush()
    return customer, profile, request


class TestRequestDetail:
    def test_detail_carries_the_context_an_administrator_decides_on(self, session):
        customer, profile, request = customer_with_request(session, 2, ["TX", "FL"])
        session.commit()
        client = as_admin(session)
        try:
            response = client.get(f"/api/admin/requests/{request.id}")
            assert response.status_code == 200
            body = response.json()
        finally:
            app.dependency_overrides.clear()

        assert body["id"] == str(request.id)
        assert body["customerIdentity"] == customer.name
        assert body["agency"] == "Northstar Agency"
        assert body["email"] == profile.email
        assert body["leadCount"] == 2
        assert body["states"] == ["FL", "TX"]
        assert body["status"] == RequestStatus.pending.value
        assert body["availableCount"] is None
        assert "history" in body

    def test_a_missing_request_is_a_404(self, session):
        client = as_admin(session)
        try:
            response = client.get(f"/api/admin/requests/{uuid.uuid4()}")
            assert response.status_code == 404
        finally:
            app.dependency_overrides.clear()

    def test_history_reads_recorded_activity_rather_than_a_second_source(
        self, session
    ):
        _, _, request = customer_with_request(session)
        session.commit()
        client = as_admin(session)
        try:
            approved = client.post(
                f"/api/admin/requests/{request.id}/approve",
                json={"reason": "Inventory verified."},
            )
            assert approved.status_code == 200
            body = client.get(f"/api/admin/requests/{request.id}").json()
        finally:
            app.dependency_overrides.clear()

        assert [entry["action"] for entry in body["history"]] == [
            "batch_request_approve"
        ]
        entry = body["history"][0]
        assert entry["reason"] == "Inventory verified."
        assert entry["actor"] == str(ADMIN_ID)
        assert entry["before"] == {"status": "pending"}
        assert entry["after"] == {"status": "approved"}

    def test_renotify_enqueues_update_notification_and_records_activity(
        self, session
    ):
        from jawnix.models import Job

        _, _, request = customer_with_request(session)
        session.commit()
        client = as_admin(session)
        try:
            response = client.post(
                f"/api/admin/requests/{request.id}/notify",
                json={"reason": "Telegram never arrived."},
            )
            assert response.status_code == 200
            body = response.json()
            missing = client.post(
                f"/api/admin/requests/{uuid.uuid4()}/notify",
                json={"reason": "missing"},
            )
        finally:
            app.dependency_overrides.clear()

        assert body["requestId"] == str(request.id)
        assert body["jobKind"] == "update_notification"
        job = session.get(Job, body["jobId"])
        assert job is not None
        assert job.kind == "update_notification"
        assert job.request_id == request.id
        audit = session.scalar(
            select(AuditEntry).where(
                AuditEntry.action == "batch_request_notify"
            )
        )
        assert audit is not None
        assert audit.target_id == str(request.id)
        assert audit.reason == "Telegram never arrived."
        assert missing.status_code == 404

    def test_detail_projects_milestones_live_activity_and_distribution(
        self, session
    ):
        from jawnix.models import DistributionEvent, Job, JobStatus, Lead

        customer, _, request = customer_with_request(session, 2, ["TX", "FL"])
        lead_a = Lead(phone="2145550101", title="A", state="TX")
        lead_b = Lead(phone="2145550102", title="B", state="FL")
        session.add_all([lead_a, lead_b])
        session.flush()
        session.add_all(
            [
                DistributionEvent(
                    lead_id=lead_a.id,
                    agent_id=customer.id,
                    request_id=request.id,
                    phone=lead_a.phone,
                    state="TX",
                    source_niche="Roofing",
                    delivered_at=datetime.now(timezone.utc),
                    source="request",
                ),
                DistributionEvent(
                    lead_id=lead_b.id,
                    agent_id=customer.id,
                    request_id=request.id,
                    phone=lead_b.phone,
                    state="FL",
                    source_niche="",
                    delivered_at=datetime.now(timezone.utc),
                    source="request",
                ),
                Job(
                    kind="update_notification",
                    request_id=request.id,
                    status=JobStatus.queued.value,
                ),
            ]
        )
        session.commit()
        client = as_admin(session)
        try:
            body = client.get(f"/api/admin/requests/{request.id}").json()
        finally:
            app.dependency_overrides.clear()

        assert body["milestones"]["current_key"] == "submitted"
        assert [m["key"] for m in body["milestones"]["milestones"]] == [
            "submitted",
            "under_review",
            "preparing_batch",
            "delivered",
        ]
        assert body["live"]["settled"] is False
        assert body["live"]["refreshSeconds"] == 10
        assert body["live"]["jobs"][0]["kind"] == "update_notification"
        assert (
            body["live"]["jobs"][0]["label"]
            == "Updating the Telegram message"
        )
        log_kinds = {row["kind"] for row in body["live"]["log"]}
        assert "job" in log_kinds
        assert "status" in log_kinds
        assert any(
            row["label"] == "Updating the Telegram message"
            for row in body["live"]["log"]
        )
        by_state = {
            (cell["state"], cell["niche"]): cell["count"]
            for cell in body["distribution"]["byState"]
        }
        assert by_state[("TX", "Roofing")] == 1
        assert by_state[("FL", "Unmapped")] == 1
        assert body["live"]["blocker"] is None

    def test_live_blocker_ignores_metrics_lane_for_telegram_jobs(self, session):
        from jawnix.models import Job, JobStatus

        _, _, waiting = customer_with_request(session)
        _, _, blocking = customer_with_request(session, 1, ["TX"])
        session.add_all(
            [
                Job(
                    kind="emit_lead_assigned",
                    request_id=blocking.id,
                    status=JobStatus.running.value,
                    locked_at=datetime.now(timezone.utc),
                    locked_by="metrics-worker-1",
                    attempts=1,
                ),
                Job(
                    kind="notify_request",
                    request_id=waiting.id,
                    status=JobStatus.queued.value,
                ),
            ]
        )
        session.commit()
        client = as_admin(session)
        try:
            body = client.get(f"/api/admin/requests/{waiting.id}").json()
        finally:
            app.dependency_overrides.clear()

        assert body["live"]["blocker"] is None

    def test_live_blocker_when_worker_busy_on_another_request(self, session):
        from jawnix.models import Job, JobStatus

        _, _, waiting = customer_with_request(session)
        _, _, blocking = customer_with_request(session, 1, ["TX"])
        session.add_all(
            [
                Job(
                    kind="fulfill_round_robin",
                    request_id=blocking.id,
                    status=JobStatus.running.value,
                    locked_at=datetime.now(timezone.utc),
                    locked_by="test-worker",
                    attempts=1,
                ),
                Job(
                    kind="notify_request",
                    request_id=waiting.id,
                    status=JobStatus.queued.value,
                ),
            ]
        )
        session.commit()
        client = as_admin(session)
        try:
            body = client.get(f"/api/admin/requests/{waiting.id}").json()
        finally:
            app.dependency_overrides.clear()

        blocker = body["live"]["blocker"]
        assert blocker is not None
        assert blocker["kind"] == "fulfill_round_robin"
        assert "another batch" in blocker["detail"]
        assert str(blocking.id) in blocker["detail"]

    def test_terminal_request_is_settled_without_active_jobs(self, session):
        _, _, request = customer_with_request(session)
        request.status = RequestStatus.delivered.value
        session.commit()
        client = as_admin(session)
        try:
            body = client.get(f"/api/admin/requests/{request.id}").json()
        finally:
            app.dependency_overrides.clear()

        assert body["live"]["settled"] is True
        assert body["live"]["jobs"] == []
        assert body["milestones"]["current_key"] == "delivered"
        assert body["milestones"]["milestones"][-1]["state"] == "complete"

    def test_waiting_inventory_is_unsettled_with_pause(self, session):
        _, _, request = customer_with_request(session)
        request.status = RequestStatus.waiting_inventory.value
        request.approved_at = datetime.now(timezone.utc)
        session.commit()
        client = as_admin(session)
        try:
            body = client.get(f"/api/admin/requests/{request.id}").json()
        finally:
            app.dependency_overrides.clear()

        assert body["live"]["settled"] is False
        assert body["milestones"]["pause"] is not None
        assert body["milestones"]["pause"]["kind"] == "inventory_wait"


class TestOfferedActions:
    def test_a_pending_request_offers_only_its_valid_actions(self, session):
        _, _, request = customer_with_request(session)
        session.commit()
        client = as_admin(session)
        try:
            body = client.get(f"/api/admin/requests/{request.id}").json()
        finally:
            app.dependency_overrides.clear()

        assert [action["name"] for action in body["actions"]] == [
            "approve",
            "reject",
            "cancel",
        ]

    def test_every_offered_action_states_its_consequence_and_wants_a_reason(
        self, session
    ):
        _, _, request = customer_with_request(session)
        session.commit()
        client = as_admin(session)
        try:
            body = client.get(f"/api/admin/requests/{request.id}").json()
        finally:
            app.dependency_overrides.clear()

        for action in body["actions"]:
            assert action["consequence"]
            assert action["requiresReason"] is True
            assert action["label"]

    def test_a_terminal_request_offers_nothing(self, session):
        _, _, request = customer_with_request(session)
        request.status = RequestStatus.rejected.value
        session.commit()
        client = as_admin(session)
        try:
            body = client.get(f"/api/admin/requests/{request.id}").json()
        finally:
            app.dependency_overrides.clear()

        assert body["actions"] == []

    def test_the_offer_and_the_enforcement_agree(self, session):
        """Every action the contract withholds is also refused if attempted."""
        _, _, request = customer_with_request(session)
        request.status = RequestStatus.delivered.value
        session.commit()
        client = as_admin(session)
        try:
            body = client.get(f"/api/admin/requests/{request.id}").json()
            offered = {action["name"] for action in body["actions"]}
            for name in ("approve", "retry", "reject", "cancel", "retry_delivery"):
                if name in offered:
                    continue
                refused = client.post(
                    f"/api/admin/requests/{request.id}/{name}",
                    json={"reason": "Should not be permitted."},
                )
                assert refused.status_code == 409, name
        finally:
            app.dependency_overrides.clear()


class TestDeliveryRecovery:
    """Retrying the exact artifact and rerunning allocation stay distinct."""

    def test_a_failed_request_with_an_artifact_offers_delivery_retry_only(
        self, session, settings
    ):
        customer, _, request = customer_with_request(
            session, status=RequestStatus.approved.value
        )
        session.add(Lead(phone="2145550111", title="Lead", state="TX"))
        session.flush()
        allocate_request(session, request.id, settings)
        request.status = RequestStatus.failed.value
        session.commit()
        client = as_admin(session)
        try:
            body = client.get(f"/api/admin/requests/{request.id}").json()
        finally:
            app.dependency_overrides.clear()

        names = [action["name"] for action in body["actions"]]
        assert "retry_delivery" in names
        # Reallocating would consume more inventory for a request whose
        # Distribution Events are already permanent.
        assert "retry" not in names

    def test_a_failed_request_without_an_artifact_offers_generation_retry_only(
        self, session
    ):
        _, _, request = customer_with_request(session)
        request.status = RequestStatus.failed.value
        session.commit()
        client = as_admin(session)
        try:
            body = client.get(f"/api/admin/requests/{request.id}").json()
        finally:
            app.dependency_overrides.clear()

        names = [action["name"] for action in body["actions"]]
        assert "retry" in names
        assert "retry_delivery" not in names

    def test_the_two_recovery_actions_are_labelled_apart(self, session, settings):
        _, _, failed = customer_with_request(session)
        failed.status = RequestStatus.failed.value
        session.commit()
        client = as_admin(session)
        try:
            body = client.get(f"/api/admin/requests/{failed.id}").json()
        finally:
            app.dependency_overrides.clear()

        retry = next(a for a in body["actions"] if a["name"] == "retry")
        assert retry["label"] == "Retry generation"

    def test_delivery_retry_preserves_the_exact_artifact(
        self, session, settings
    ):
        _, _, request = customer_with_request(
            session, status=RequestStatus.approved.value
        )
        session.add(Lead(phone="2145550112", title="Lead", state="TX"))
        session.flush()
        allocate_request(session, request.id, settings)
        request.status = RequestStatus.failed.value
        session.commit()
        artifact = session.scalar(
            select(BatchArtifact).where(BatchArtifact.request_id == request.id)
        )
        original = artifact.sha256

        client = as_admin(session)
        try:
            response = client.post(
                f"/api/admin/requests/{request.id}/retry_delivery",
                json={"reason": "Provider outage cleared."},
            )
            assert response.status_code == 200
        finally:
            app.dependency_overrides.clear()

        session.refresh(artifact)
        assert artifact.sha256 == original
        assert request.status == RequestStatus.generated.value


class TestArtifactRegeneration:
    def test_regenerate_is_offered_only_once_the_file_has_expired(
        self, session, settings
    ):
        _, _, request = customer_with_request(
            session, status=RequestStatus.approved.value
        )
        session.add(Lead(phone="2145550113", title="Lead", state="TX"))
        session.flush()
        allocate_request(session, request.id, settings)
        request.status = RequestStatus.delivered.value
        session.commit()
        artifact = session.scalar(
            select(BatchArtifact).where(BatchArtifact.request_id == request.id)
        )

        client = as_admin(session)
        try:
            live = client.get(f"/api/admin/requests/{request.id}").json()
            assert "regenerate" not in [a["name"] for a in live["actions"]]

            artifact.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
            Path(artifact.path).unlink()
            session.commit()

            expired = client.get(f"/api/admin/requests/{request.id}").json()
            assert "regenerate" in [a["name"] for a in expired["actions"]]
        finally:
            app.dependency_overrides.clear()

    def test_a_regenerable_artifact_is_reachable_from_the_workspace(
        self, session, settings
    ):
        """A delivered request is settled work, so it appears nowhere else."""
        _, _, request = customer_with_request(
            session, status=RequestStatus.approved.value
        )
        session.add(Lead(phone="2145550117", title="Lead", state="TX"))
        session.flush()
        allocate_request(session, request.id, settings)
        request.status = RequestStatus.delivered.value
        artifact = session.scalar(
            select(BatchArtifact).where(BatchArtifact.request_id == request.id)
        )
        artifact.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        Path(artifact.path).unlink()
        session.commit()

        client = as_admin(session)
        try:
            body = client.get("/api/admin/fulfillment").json()
        finally:
            app.dependency_overrides.clear()

        listed = next(
            item
            for item in body["expiredArtifacts"]
            if item["id"] == str(request.id)
        )
        assert "regenerate" in [a["name"] for a in listed["actions"]]
        # It is settled work, so it must not also sit in the live queue.
        assert str(request.id) not in [
            item["id"] for item in body["batchRequests"]
        ]

    def test_a_live_artifact_stays_out_of_the_regeneration_queue(
        self, session, settings
    ):
        _, _, request = customer_with_request(
            session, status=RequestStatus.approved.value
        )
        session.add(Lead(phone="2145550118", title="Lead", state="TX"))
        session.flush()
        allocate_request(session, request.id, settings)
        request.status = RequestStatus.delivered.value
        session.commit()

        client = as_admin(session)
        try:
            body = client.get("/api/admin/fulfillment").json()
        finally:
            app.dependency_overrides.clear()

        assert body["expiredArtifacts"] == []


class TestOfferMatchesEnforcementOverHTTP:
    """The invariant in both directions, not just offered-implies-accepted."""

    def test_a_withheld_retry_is_also_refused_by_the_endpoint(
        self, session, settings
    ):
        # A failed request with an artifact must not reallocate: its
        # Distribution Events are permanent (docs/adr/0003).
        _, _, request = customer_with_request(
            session, status=RequestStatus.approved.value
        )
        session.add(Lead(phone="2145550115", title="Lead", state="TX"))
        session.flush()
        allocate_request(session, request.id, settings)
        request.status = RequestStatus.failed.value
        session.commit()

        client = as_admin(session)
        try:
            body = client.get(f"/api/admin/requests/{request.id}").json()
            assert "retry" not in [a["name"] for a in body["actions"]]
            refused = client.post(
                f"/api/admin/requests/{request.id}/retry",
                json={"reason": "Should not reallocate."},
            )
        finally:
            app.dependency_overrides.clear()

        assert refused.status_code == 409
        assert request.status == RequestStatus.failed.value

    def test_a_reason_is_required_rather_than_synthesised(self, session):
        _, _, request = customer_with_request(session)
        session.commit()
        client = as_admin(session)
        try:
            missing = client.post(f"/api/admin/requests/{request.id}/approve")
            empty = client.post(
                f"/api/admin/requests/{request.id}/approve",
                json={"reason": ""},
            )
        finally:
            app.dependency_overrides.clear()

        assert missing.status_code == 422
        assert empty.status_code == 422
        assert request.status == RequestStatus.pending.value


class TestAdminCancel:
    def test_an_administrator_can_cancel_an_uncommitted_request(self, session):
        _, _, request = customer_with_request(session)
        session.commit()
        client = as_admin(session)
        try:
            response = client.post(
                f"/api/admin/requests/{request.id}/cancel",
                json={"reason": "Customer withdrew by phone."},
            )
            assert response.status_code == 200
        finally:
            app.dependency_overrides.clear()

        assert request.status == RequestStatus.canceled.value

    def test_cancelling_is_recorded_through_the_shared_activity_seam(self, session):
        _, _, request = customer_with_request(session)
        session.commit()
        client = as_admin(session)
        try:
            client.post(
                f"/api/admin/requests/{request.id}/cancel",
                json={"reason": "Customer withdrew by phone."},
            )
        finally:
            app.dependency_overrides.clear()

        entry = session.scalar(
            select(AuditEntry).where(AuditEntry.action == "batch_request_cancel")
        )
        assert entry is not None
        assert entry.reason == "Customer withdrew by phone."
        assert entry.actor_user_id == str(ADMIN_ID)
        assert entry.target_type == "batch_request"
        assert entry.details["after"] == {"status": "canceled"}

    def test_an_approved_request_cannot_be_cancelled_after_distribution_commits(
        self, session, settings
    ):
        """Status alone would allow this; committed history must not."""
        _, _, request = customer_with_request(
            session, status=RequestStatus.approved.value
        )
        session.add(Lead(phone="2145550116", title="Lead", state="TX"))
        session.flush()
        allocate_request(session, request.id, settings)
        # Put it back to approved: the row a mid-rotation cancel would race.
        request.status = RequestStatus.approved.value
        session.commit()

        client = as_admin(session)
        try:
            response = client.post(
                f"/api/admin/requests/{request.id}/cancel",
                json={"reason": "Customer changed their mind."},
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 409
        assert request.status == RequestStatus.approved.value

    def test_a_delivered_request_cannot_be_cancelled(self, session):
        _, _, request = customer_with_request(session)
        request.status = RequestStatus.delivered.value
        session.commit()
        client = as_admin(session)
        try:
            response = client.post(
                f"/api/admin/requests/{request.id}/cancel",
                json={"reason": "Too late."},
            )
            assert response.status_code == 409
        finally:
            app.dependency_overrides.clear()

        assert request.status == RequestStatus.delivered.value


class TestInventoryConflicts:
    def conflict(self, session):
        _, _, older = customer_with_request(session, 2)
        _, _, newer = customer_with_request(session, 1)
        older.status = RequestStatus.waiting_inventory.value
        newer.status = RequestStatus.waiting_inventory.value
        conflict = InventoryConflict(
            older_request_id=older.id,
            newer_request_id=newer.id,
            inventory_snapshot={
                "olderRequestId": str(older.id),
                "newerRequestId": str(newer.id),
                "olderLeadCount": 2,
                "newerLeadCount": 1,
                "olderStates": ["TX"],
                "newerStates": ["TX"],
                "olderEligibleCount": 1,
                "newerEligibleCount": 1,
                "sharedLeadIds": [1],
            },
            snapshot_checksum="a" * 64,
            status="pending",
        )
        session.add(conflict)
        session.commit()
        return older, newer, conflict

    def test_detail_explains_the_competing_requests_and_decision_scope(
        self, session
    ):
        older, newer, conflict = self.conflict(session)
        client = as_admin(session)
        try:
            response = client.get(f"/api/admin/inventory-conflicts/{conflict.id}")
            assert response.status_code == 200
            body = response.json()
        finally:
            app.dependency_overrides.clear()

        assert body["olderRequest"]["id"] == str(older.id)
        assert body["newerRequest"]["id"] == str(newer.id)
        assert body["overlappingLeadCount"] == 1
        assert body["snapshotChecksum"] == "a" * 64
        assert body["status"] == "pending"
        assert [action["name"] for action in body["actions"]] == ["confirm", "deny"]
        # The recurrence rule is what stops an administrator expecting a
        # denial to be revisited on the same snapshot.
        assert "material change" in body["recurrenceRule"]

    def test_a_decided_conflict_offers_no_further_decision(self, session):
        _, _, conflict = self.conflict(session)
        conflict.status = "denied"
        session.commit()
        client = as_admin(session)
        try:
            body = client.get(
                f"/api/admin/inventory-conflicts/{conflict.id}"
            ).json()
        finally:
            app.dependency_overrides.clear()

        assert body["actions"] == []

    def test_the_workspace_surfaces_pending_conflicts(self, session):
        _, _, conflict = self.conflict(session)
        client = as_admin(session)
        try:
            body = client.get("/api/admin/fulfillment").json()
        finally:
            app.dependency_overrides.clear()

        assert [item["id"] for item in body["inventoryConflicts"]] == [
            str(conflict.id)
        ]

    def test_a_decided_conflict_leaves_the_workspace(self, session):
        _, _, conflict = self.conflict(session)
        conflict.status = "denied"
        session.commit()
        client = as_admin(session)
        try:
            body = client.get("/api/admin/fulfillment").json()
        finally:
            app.dependency_overrides.clear()

        assert body["inventoryConflicts"] == []

    def test_a_second_decision_on_the_same_conflict_is_a_duplicate(self, session):
        _, _, conflict = self.conflict(session)
        client = as_admin(session)
        try:
            first = client.post(
                f"/api/admin/inventory-conflicts/{conflict.id}/deny",
                json={"reason": "Preserve the older request."},
            )
            second = client.post(
                f"/api/admin/inventory-conflicts/{conflict.id}/deny",
                json={"reason": "Duplicate submission."},
            )
        finally:
            app.dependency_overrides.clear()

        assert first.json()["status"] == "denied"
        assert second.json()["duplicate"] is True


class TestWorkspace:
    def test_the_workspace_gathers_requests_conflicts_and_delivery_failures(
        self, session, settings
    ):
        _, _, pending = customer_with_request(session)
        _, _, failing = customer_with_request(
            session, status=RequestStatus.approved.value
        )
        session.add(Lead(phone="2145550114", title="Lead", state="TX"))
        session.flush()
        allocate_request(session, failing.id, settings)
        failing.status = RequestStatus.failed.value
        artifact = session.scalar(
            select(BatchArtifact).where(BatchArtifact.request_id == failing.id)
        )
        artifact.delivery_status = "failed"
        artifact.last_error = "Provider returned HTTP 503"
        session.commit()

        client = as_admin(session)
        try:
            body = client.get("/api/admin/fulfillment").json()
        finally:
            app.dependency_overrides.clear()

        assert str(pending.id) in [item["id"] for item in body["batchRequests"]]
        failure = next(
            item for item in body["deliveryFailures"] if item["id"] == str(failing.id)
        )
        assert failure["lastError"] == "Provider returned HTTP 503"
        assert "inventoryConflicts" in body

    def test_settled_requests_stay_out_of_the_work_queue(self, session):
        _, _, delivered = customer_with_request(session)
        delivered.status = RequestStatus.delivered.value
        session.commit()
        client = as_admin(session)
        try:
            body = client.get("/api/admin/fulfillment").json()
        finally:
            app.dependency_overrides.clear()

        assert str(delivered.id) not in [
            item["id"] for item in body["batchRequests"]
        ]

    def test_every_queued_request_carries_its_valid_actions(self, session):
        customer_with_request(session)
        session.commit()
        client = as_admin(session)
        try:
            body = client.get("/api/admin/fulfillment").json()
        finally:
            app.dependency_overrides.clear()

        for item in body["batchRequests"]:
            assert item["actions"]
            for action in item["actions"]:
                assert action["name"] in {
                    "approve",
                    "retry",
                    "retry_delivery",
                    "regenerate",
                    "reject",
                    "cancel",
                }
