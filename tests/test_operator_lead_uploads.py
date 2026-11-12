"""The operator's DB2 uploads, and the retired managed administration.

The operator uploads CSV leads into DB2 at /api/admin/lead-uploads. Managed
administrators (Alex) once uploaded there too. Their access is gone, but
their uploads still name them.
"""
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import Response
from fastapi.testclient import TestClient
from sqlalchemy import select

from jawnix.api import app
from jawnix.auth import RETIRED_MANAGER_MESSAGE, administration_home, issue_session
from jawnix.config import get_settings
from jawnix.database import get_db
from jawnix.models import AdminMFAState, AuditEntry, Customer, Job, LeadUpload, ManagedAdmin


@pytest.fixture
def uploads(session, settings, monkeypatch):
    manager_id, operator_id = uuid.uuid4(), uuid.uuid4()
    # After the retirement migration every managed administrator is inactive.
    alex = ManagedAdmin(auth_user_id=manager_id, name="Alex", email="alex@example.test", active=False)
    session.add_all([alex, AdminMFAState(user_id=operator_id, session_generation=1)])
    session.flush()
    earlier = LeadUpload(manager_id=manager_id, filename="alex.csv", state="TN", path="alex.csv", status="complete")
    customer = Customer(slug="harbor", name="Harbor", licensed_states=["TN"])
    session.add_all([earlier, customer])
    session.commit()
    factor_id = uuid.uuid4()

    class Provider:
        async def request(self, action, **payload):
            raise AssertionError(f"No live provider call is expected: {action}")

        async def list_factors(self, user_id, **_):
            return [SimpleNamespace(verified_totp=True, id=factor_id)]

    monkeypatch.setattr("jawnix.auth.get_mfa_provider", lambda _: Provider())

    def db():
        yield session

    app.dependency_overrides[get_db] = db
    app.dependency_overrides[get_settings] = lambda: settings

    def client(role="admin"):
        user_id = operator_id if role == "admin" else manager_id
        response = Response()
        principal = issue_session(
            response,
            {"id": str(user_id), "email": f"{role}@example.test", "app_metadata": {"jawnix_role": role},
             "_jawnix_auth_claims": {"sessionId": str(uuid.uuid4())}},
            settings, assurance="aal2", session_generation=1, factor_id=factor_id,
        )
        value = TestClient(app)
        for header, content in response.raw_headers:
            if header == b"set-cookie":
                key, token = content.decode().split(";", 1)[0].split("=", 1)
                value.cookies.set(key, token)
        value.headers["X-CSRF-Token"] = principal.csrf
        return value

    yield SimpleNamespace(manager_id=manager_id, operator_id=operator_id, earlier_id=earlier.id,
                          customer_id=customer.id, client=client)
    app.dependency_overrides.clear()


def test_operator_uploads_into_db2_and_sees_earlier_managed_uploads(uploads, session):
    content = b"Phone,Name\n+1 (615) 555-0101,Operator Lead\n"
    with uploads.client() as operator:
        response = operator.post("/api/admin/lead-uploads", files={"file": ("operator.csv", content)}, data={"state": "KY"})
        assert response.status_code == 202
        value = response.json()
        assert value["status"] == "queued" and value["uploadedBy"] == "Operator"
        row = session.get(LeadUpload, uuid.UUID(value["id"]))
        assert row.operator_id == uploads.operator_id and row.manager_id is None and row.uploader_id == uploads.operator_id
        assert row.state == "KY" and Path(row.path).read_bytes() == content
        assert Path(row.path).stat().st_mode & 0o777 == 0o600
        assert any(job.payload == {"upload_id": value["id"]} for job in session.scalars(select(Job).where(Job.kind == "import_lead_upload")))
        audit = session.scalar(select(AuditEntry).where(AuditEntry.action == "operator_lead_upload_submitted"))
        assert audit is not None and audit.actor_user_id == str(uploads.operator_id)

        listed = operator.get("/api/admin/lead-uploads").json()["uploads"]
        # Alex is inactive, and his earlier upload still names him.
        assert {item["id"]: item["uploadedBy"] for item in listed} == {value["id"]: "Operator", str(uploads.earlier_id): "Alex"}
        assert operator.get(f"/api/admin/lead-uploads/{uploads.earlier_id}").json()["uploadedBy"] == "Alex"
        assert operator.post(f"/api/admin/lead-uploads/{value['id']}/retry").status_code == 409


def test_operator_upload_requires_a_state_and_a_csv(uploads):
    with uploads.client() as operator:
        assert operator.post("/api/admin/lead-uploads", files={"file": ("x.csv", b"Phone\n6155550100\n")}, data={"state": "ZZ"}).status_code == 422
        assert operator.post("/api/admin/lead-uploads", files={"file": ("x.txt", b"Phone\n6155550100\n")}, data={"state": "TN"}).status_code == 422


def test_operator_retries_a_failed_upload_and_downloads_its_rejected_rows(uploads, session, tmp_path):
    source = tmp_path / "failed.csv"
    source.write_text("Phone\n6155550100\nnot-a-phone\n")
    source.with_suffix(".errors.csv").write_text("row,error\n3,Invalid phone\n")
    failed = LeadUpload(operator_id=uploads.operator_id, filename="failed.csv", state="TN", path=str(source),
                        status="failed", error="Interrupted")
    session.add(failed)
    session.commit()
    with uploads.client() as operator:
        rejected = operator.get(f"/api/admin/lead-uploads/{failed.id}/rejected")
        assert rejected.status_code == 200 and "Invalid phone" in rejected.text
        retried = operator.post(f"/api/admin/lead-uploads/{failed.id}/retry")
        assert retried.status_code == 200 and retried.json()["status"] == "queued"
        assert operator.get(f"/api/admin/lead-uploads/{uuid.uuid4()}").status_code == 404


def test_managed_administration_endpoints_are_gone(uploads):
    with uploads.client() as operator:
        for method, path in (
            ("GET", "/api/manager/access"),
            ("GET", "/api/manager/customers"),
            ("POST", "/api/manager/customers"),
            ("GET", "/api/manager/uploads"),
            ("POST", "/api/manager/uploads"),
            ("GET", f"/api/manager/uploads/{uploads.earlier_id}"),
            ("GET", f"/api/manager/uploads/{uploads.earlier_id}/rejected"),
            ("POST", f"/api/manager/uploads/{uploads.earlier_id}/retry"),
            ("POST", f"/api/admin/managed-admins/{uploads.manager_id}/access-link"),
        ):
            assert operator.request(method, path).status_code == 404, path
        # A3 replaced the legacy scope route with PUT .../lead-database.
        legacy = operator.patch(f"/api/admin/customers/{uploads.customer_id}/scope", json={"lead_database": "db2"})
        assert legacy.status_code in {404, 405}
        directory = operator.get("/api/admin/customers/directory").json()
        assert "managed_admins" not in directory
        row = next(item for item in directory["customers"] if item["id"] == uploads.customer_id)
        assert "allowed_manager_id" not in row and "manager_id" not in row
        details = operator.get(f"/api/admin/customers/{uploads.customer_id}/details").json()
        assert "allowed_manager_id" not in details["customer"] and "manager_id" not in details["customer"]


def test_a_manager_session_issued_before_retirement_is_refused(uploads):
    with uploads.client("manager") as manager:
        for path in ("/api/auth/destination", "/api/me/registration", "/api/admin/lead-uploads"):
            refused = manager.get(path)
            assert refused.status_code == 401, path
            assert refused.json()["detail"] == RETIRED_MANAGER_MESSAGE


def test_a_manager_identity_cannot_sign_in(uploads, session, monkeypatch):
    async def verify(_token, _settings):
        return {"id": str(uploads.manager_id), "email": "alex@example.test",
                "app_metadata": {"jawnix_role": "manager"}, "user_metadata": {}}

    monkeypatch.setattr("jawnix.api.verify_access_token", verify)
    # Even a managed administrator row someone reactivated by hand grants nothing.
    session.get(ManagedAdmin, uploads.manager_id).active = True
    session.commit()
    with TestClient(app) as client:
        refused = client.post("/api/auth/session", json={"access_token": "a-provider-token-long-enough",
                                                         "requested_next": "/app/manager/uploads"})
        assert refused.status_code == 403
        assert refused.json()["detail"] == RETIRED_MANAGER_MESSAGE
        assert "jawnix_session" not in refused.cookies


def test_administration_always_lands_on_the_operator_home():
    for role in ("admin", "manager"):
        principal = SimpleNamespace(role=role)
        assert administration_home(principal) == "/app/admin/today"
