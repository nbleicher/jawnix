import uuid
from types import SimpleNamespace

import pytest
from fastapi import Response
from fastapi.testclient import TestClient
from sqlalchemy import select

from jawnix.api import app
from jawnix.auth import issue_session
from jawnix.config import get_settings
from jawnix.database import get_db
from jawnix.models import AdminMFAState, Customer, Job, LeadUpload, ManagedAdmin


@pytest.fixture
def managed_setup(session, settings, monkeypatch):
    manager_id, operator_id = uuid.uuid4(), uuid.uuid4()
    manager = ManagedAdmin(auth_user_id=manager_id, name="Alex", email="alex@example.test")
    allowed = Customer(slug="allowed", name="Allowed", licensed_states=["TN"], allowed_manager_id=manager_id)
    other = Customer(slug="other", name="Other", licensed_states=["TN"])
    session.add_all([manager, allowed, other, AdminMFAState(user_id=manager_id, session_generation=1), AdminMFAState(user_id=operator_id, session_generation=1)])
    session.commit()
    factor_id = uuid.uuid4()

    class Provider:
        async def request(self, action, **payload):
            assert action == "validate-session" and payload["role"] == "manager"
            return {"valid": True}

        async def list_factors(self, user_id):
            return [SimpleNamespace(verified_totp=True, id=factor_id)]

    monkeypatch.setattr("jawnix.auth.get_mfa_provider", lambda _: Provider())
    def db():
        yield session
    app.dependency_overrides[get_db] = db
    app.dependency_overrides[get_settings] = lambda: settings
    def client(role="manager", assurance="aal2"):
        user_id = manager_id if role == "manager" else operator_id
        response = Response()
        principal = issue_session(response, {"id":str(user_id), "email":f"{role}@example.test", "app_metadata":{"jawnix_role":role}, "_jawnix_auth_claims":{"sessionId":str(uuid.uuid4())}},
                                  settings, assurance=assurance, session_generation=1, factor_id=factor_id)
        value = TestClient(app)
        for header, content in response.raw_headers:
            if header == b"set-cookie":
                key, token = content.decode().split(";", 1)[0].split("=", 1)
                value.cookies.set(key, token)
        value.headers["X-CSRF-Token"] = principal.csrf
        return value
    yield SimpleNamespace(manager_id=manager_id, operator_id=operator_id, allowed_id=allowed.id, other_id=other.id, client=client)
    app.dependency_overrides.clear()


def test_manager_only_sees_and_assigns_operator_approved_customers(managed_setup, session):
    scope = managed_setup
    with scope.client() as client:
        data = client.get("/api/manager/customers").json()
        assert data["customers"] == []
        assert [row["id"] for row in data["available"]] == [scope.allowed_id]
        denied = client.post("/api/manager/customers", json={"customer_ids":[scope.allowed_id, scope.other_id]})
        assert denied.status_code == 404
        assert session.get(Customer, scope.allowed_id).manager_id is None
        assigned = client.post("/api/manager/customers", json={"customer_ids":[scope.allowed_id]})
        assert assigned.status_code == 200
        assert [row["id"] for row in assigned.json()["customers"]] == [scope.allowed_id]
        assert client.get("/api/admin/customers/directory").status_code == 403
        assert client.patch(f"/api/admin/customers/{scope.allowed_id}/scope", json={"lead_database":"db2"}).status_code == 403


def test_operator_removal_prevents_manager_from_readding_customer(managed_setup, session):
    scope = managed_setup
    with scope.client("admin") as operator:
        assert operator.patch(f"/api/admin/customers/{scope.allowed_id}/scope", json={"manager_id":str(scope.manager_id)}).status_code == 200
        removed = operator.patch(f"/api/admin/customers/{scope.allowed_id}/scope", json={"allowed_manager_id":None})
        assert removed.status_code == 200
        assert removed.json()["manager_id"] is None
    with scope.client() as manager:
        assert manager.get("/api/manager/customers").json() == {"customers":[], "available":[]}
        assert manager.post("/api/manager/customers", json={"customer_ids":[scope.allowed_id]}).status_code == 404


def test_source_change_is_customer_scoped_and_queues_fulfillment(managed_setup, session):
    scope = managed_setup
    with scope.client("admin") as operator:
        result = operator.patch(f"/api/admin/customers/{scope.allowed_id}/scope", json={"lead_database":"db2"})
        assert result.status_code == 200
        directory = operator.get("/api/admin/customers/directory").json()
    assert result.json()["lead_database"] == "db2"
    assert next(row for row in directory["customers"] if row["id"] == scope.allowed_id)["lead_database"] == "db2"
    assert session.get(Customer, scope.other_id).lead_database == "db1"
    assert session.scalar(select(Job).where(Job.kind == "fulfill_round_robin")) is not None


def test_password_only_manager_uploads_owned_file_and_cannot_enroll_mfa(managed_setup, session):
    scope = managed_setup
    content = b"Phone,Name\n+1 (615) 555-0100,Example\n"
    with scope.client(assurance="aal1") as unverified:
        assert unverified.get("/api/auth/admin-mfa").status_code == 403
    with scope.client(assurance="aal1") as client:
        response = client.post("/api/manager/uploads", files={"file":("leads.csv",content)}, data={"state":"TN"})
        assert response.status_code == 202
        value = response.json()
        assert value["status"] == "queued" and value["processedRows"] == 0
        row = session.get(LeadUpload, uuid.UUID(value["id"]))
        assert row.manager_id == scope.manager_id and row.state == "TN"
        from pathlib import Path
        assert Path(row.path).read_bytes() == content
        assert Path(row.path).stat().st_mode & 0o777 == 0o600
        assert client.get("/api/manager/uploads").json()["uploads"][0]["id"] == value["id"]
        other = ManagedAdmin(auth_user_id=uuid.uuid4(), name="Private", email="private@example.test")
        session.add(other); session.flush()
        hidden = LeadUpload(manager_id=other.auth_user_id, filename="private.csv", state="TX", path="private", status="queued")
        session.add(hidden); session.commit()
        assert client.get(f"/api/manager/uploads/{hidden.id}").status_code == 404
        assert client.get(f"/api/manager/uploads/{hidden.id}/rejected").status_code == 404
