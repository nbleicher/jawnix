from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from typer.testing import CliRunner

from jawnix.config import Settings
from jawnix.customer_accounts import accept_user_account_invitation
from jawnix.database import Base
from jawnix.models import (
    Agency,
    Agent,
    AuditEntry,
    CustomerProfile,
    DistributionEvent,
    Lead,
    UserAccount,
    UserAccountInvitation,
    UserAccountMigrationArtifact,
    UserAccountMigrationMapping,
    UserAccountMigrationRun,
)
from jawnix_data.cli import app
from jawnix_data.user_account_migration import (
    APPLY_CONFIRMATION,
    MigrationRefused,
    ProviderIdentity,
    apply_user_account_migration,
    dry_run_user_account_migration,
)


class FakeIdentityProvider:
    def __init__(self, *, fail_after_create_on_call: int | None = None):
        self.identities: dict[str, ProviderIdentity] = {}
        self.invite_calls = 0
        self.fail_after_create_on_call = fail_after_create_on_call

    def list_users(self) -> list[ProviderIdentity]:
        return list(self.identities.values())

    def invite(self, email, *, run_id, mapping_id):
        self.invite_calls += 1
        identity = ProviderIdentity(
            id=uuid.uuid4(),
            email=email,
            metadata={
                "jawnix_migration_run_id": str(run_id),
                "jawnix_migration_mapping_id": str(mapping_id),
            },
        )
        self.identities[email] = identity
        if self.invite_calls == self.fail_after_create_on_call:
            raise RuntimeError("provider timed out after accepting invitation")
        return identity


@pytest.fixture
def migration_store(tmp_path):
    database = tmp_path / "migration.db"
    engine = create_engine(f"sqlite:///{database}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory, database
    engine.dispose()


def seed_customer(
    factory,
    *,
    slug: str,
    name: str,
    agency: Agency,
    old_email: str,
    phone: str,
):
    with factory.begin() as session:
        customer = Agent(
            slug=slug,
            name=name,
            licensed_states=["TX"],
            agency=agency,
        )
        old_id = uuid.uuid4()
        account = UserAccount(
            auth_user_id=old_id,
            customer=customer,
            email=old_email,
            active=True,
        )
        profile = CustomerProfile(
            user_id=old_id,
            email=old_email,
            licensed_states=["TX"],
            agent=customer,
            mapping_confirmed_at=datetime.now(timezone.utc),
        )
        lead = Lead(phone=phone, title=f"{name} Lead", state="TX")
        session.add_all([customer, account, profile, lead])
        session.flush()
        event = DistributionEvent(
            lead_id=lead.id,
            agent_id=customer.id,
            customer_name=customer.name,
            agency_id=agency.id,
            agency_name=agency.name,
            phone=phone,
            title=lead.title,
            state="TX",
            source="legacy",
        )
        session.add(event)
        session.flush()
        return customer.id, old_id, event.id


def mapping_csv(path: Path, rows: list[tuple[str, str, str]]) -> Path:
    path.write_text(
        "customer,email,agency\n"
        + "".join(f"{customer},{email},{agency}\n" for customer, email, agency in rows),
        encoding="utf-8",
    )
    return path


def backup_receipt(path: Path) -> Path:
    if path.exists():
        return path
    now = datetime.now(timezone.utc).isoformat()
    path.write_text(
        json.dumps(
            {
                "databaseSnapshot": "restic:production-20260729T120000Z",
                "databaseDumpSha256": "a" * 64,
                "resticCheckCompletedAt": now,
                "restoreRehearsalReference": "REHEARSAL-61-20260729",
                "verifiedAt": now,
                "verifiedBy": "Named verifier",
            }
        ),
        encoding="utf-8",
    )
    return path


def settings(tmp_path: Path) -> Settings:
    return Settings(
        JAWNIX_BATCH_DIR=tmp_path / "batches",
        JAWNIX_COOKIE_SECURE=False,
        JAWNIX_SESSION_SECRET="test-secret-at-least-long-enough",
    )


def apply(factory, provider, tmp_path, source, plan):
    return apply_user_account_migration(
        factory,
        provider,
        settings(tmp_path),
        source,
        approved_plan_checksum=plan["planChecksum"],
        backup_receipt_path=backup_receipt(tmp_path / "backup.json"),
        artifact_dir=tmp_path / "artifacts",
        operator="Migration Operator",
        reason="Approved one-time User Account cutover",
        confirmation=APPLY_CONFIRMATION,
    )


def test_dry_run_reports_matches_agency_impact_and_history_without_mutation(
    migration_store,
    tmp_path,
):
    factory, _ = migration_store
    with factory.begin() as session:
        current = Agency(slug="current", name="Current")
        destination = Agency(slug="destination", name="Destination")
        session.add_all([current, destination])
    customer_id, _, _ = seed_customer(
        factory,
        slug="durable-customer",
        name="Durable Customer",
        agency=current,
        old_email="old@example.com",
        phone="2155550100",
    )
    source = mapping_csv(
        tmp_path / "mappings.csv",
        [("durable-customer", "fresh@example.com", "destination")],
    )
    provider = FakeIdentityProvider()

    with factory() as session:
        before = {
            "runs": session.scalar(
                select(func.count(UserAccountMigrationRun.id))
            ),
            "audit": session.scalar(select(func.count(AuditEntry.id))),
            "accounts": session.scalar(select(func.count(UserAccount.auth_user_id))),
        }
        report = dry_run_user_account_migration(session, provider, source)
        after = {
            "runs": session.scalar(
                select(func.count(UserAccountMigrationRun.id))
            ),
            "audit": session.scalar(select(func.count(AuditEntry.id))),
            "accounts": session.scalar(select(func.count(UserAccount.auth_user_id))),
        }

    assert report["mutationPerformed"] is False
    assert report["summary"] == {
        "valid": True,
        "inputRows": 1,
        "readyRows": 1,
        "blockers": 0,
    }
    assert report["rows"][0]["customer"]["id"] == customer_id
    assert report["rows"][0]["membershipImpact"] == {
        "currentAgencyId": current.id,
        "destinationAgencyId": destination.id,
        "willChange": True,
        "permanentHistoryWillMerge": True,
    }
    assert report["rows"][0]["historyCounts"]["customerDistributionEvents"] == 1
    assert len(report["planChecksum"]) == 64
    assert before == after
    assert provider.invite_calls == 0


def test_dry_run_blocks_ambiguous_missing_duplicate_and_account_conflicts(
    migration_store,
    tmp_path,
):
    factory, _ = migration_store
    with factory.begin() as session:
        agency = Agency(slug="closed", name="Closed", active=False)
        first = Agent(slug="first", name="Shared", licensed_states=["TX"])
        second = Agent(slug="second", name="Shared", licensed_states=["TX"])
        session.add_all([agency, first, second])
    provider = FakeIdentityProvider()
    provider.identities["used@example.com"] = ProviderIdentity(
        id=uuid.uuid4(),
        email="used@example.com",
        metadata={},
    )
    source = mapping_csv(
        tmp_path / "conflicts.csv",
        [
            ("Shared", "used@example.com", "closed"),
            ("missing", "used@example.com", "missing-agency"),
        ],
    )

    with factory() as session:
        report = dry_run_user_account_migration(session, provider, source)

    codes = {
        issue["code"]
        for row in report["rows"]
        for issue in row["issues"]
    }
    assert report["summary"]["valid"] is False
    assert {
        "ambiguous_customer",
        "missing_customer",
        "duplicate_email",
        "account_conflict",
        "inactive_agency",
        "missing_agency",
    }.issubset(codes)
    assert len(report["coverage"]["unmappedCustomers"]) == 2


def test_apply_preserves_customer_and_distribution_ids_until_acceptance(
    migration_store,
    tmp_path,
):
    factory, _ = migration_store
    with factory.begin() as session:
        current = Agency(slug="current", name="Current")
        destination = Agency(slug="destination", name="Destination")
        session.add_all([current, destination])
    customer_id, old_id, event_id = seed_customer(
        factory,
        slug="preserved",
        name="Preserved",
        agency=current,
        old_email="old@example.com",
        phone="2155550101",
    )
    source = mapping_csv(
        tmp_path / "valid.csv",
        [("preserved", "new@example.com", "destination")],
    )
    provider = FakeIdentityProvider()
    with factory() as session:
        plan = dry_run_user_account_migration(session, provider, source)

    result = apply(factory, provider, tmp_path, source, plan)

    with factory() as session:
        customer = session.get(Agent, customer_id)
        event = session.get(DistributionEvent, event_id)
        old = session.get(UserAccount, old_id)
        invitation = session.scalar(select(UserAccountInvitation))
        artifact = session.scalar(select(UserAccountMigrationArtifact))
        assert customer.id == customer_id
        assert customer.agency_id == destination.id
        assert event.id == event_id
        assert event.agent_id == customer_id
        assert event.agency_id == current.id
        assert old.active is True
        assert invitation.status == "pending"
        assert invitation.replaces_auth_user_id == old_id
        assert artifact.contents["mappings"][0]["deactivation"]["state"] == (
            "deferred_until_acceptance"
        )
        assert artifact.contents["mappings"][0]["identifiers"] == {
            "customerIdPreserved": customer_id,
            "distributionEventsRewritten": 0,
        }
        assert artifact.contents["mappings"][0]["historyCounts"][
            "reconciled"
        ]["customerDistributionEvents"] == 1
        artifact_checksum = artifact.checksum

        accept_user_account_invitation(
            session,
            auth_user_id=invitation.auth_user_id,
            email=invitation.email,
        )
        session.commit()
        assert session.get(UserAccount, old_id).active is False

    artifact_path = Path(result["artifactPath"])
    assert artifact_path.stat().st_mode & 0o222 == 0
    assert json.loads(artifact_path.read_text())["artifactSha256"] == (
        artifact_checksum
    )

    rerun = apply(factory, provider, tmp_path, source, plan)
    assert rerun["idempotentRerun"] is True
    assert rerun["artifactSha256"] == artifact_checksum
    assert provider.invite_calls == 1


def test_partial_provider_failure_resumes_without_duplicate_invitation(
    migration_store,
    tmp_path,
):
    factory, _ = migration_store
    with factory.begin() as session:
        agency = Agency(slug="summit", name="Summit")
        session.add(agency)
    seed_customer(
        factory,
        slug="one",
        name="One",
        agency=agency,
        old_email="old-one@example.com",
        phone="2155550102",
    )
    seed_customer(
        factory,
        slug="two",
        name="Two",
        agency=agency,
        old_email="old-two@example.com",
        phone="2155550103",
    )
    source = mapping_csv(
        tmp_path / "partial.csv",
        [
            ("one", "new-one@example.com", "summit"),
            ("two", "new-two@example.com", "summit"),
        ],
    )
    provider = FakeIdentityProvider(fail_after_create_on_call=2)
    with factory() as session:
        plan = dry_run_user_account_migration(session, provider, source)

    with pytest.raises(RuntimeError, match="timed out"):
        apply(factory, provider, tmp_path, source, plan)
    with factory() as session:
        states = list(
            session.scalars(
                select(UserAccountMigrationMapping.state).order_by(
                    UserAccountMigrationMapping.row_number
                )
            )
        )
        assert states == ["invited_pending", "failed"]

    receipt_path = tmp_path / "backup.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["databaseSnapshot"] = "restic:partial-migration-resume"
    receipt["verifiedAt"] = datetime.now(timezone.utc).isoformat()
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    result = apply(factory, provider, tmp_path, source, plan)
    assert result["status"] == "completed"
    assert provider.invite_calls == 2
    with factory() as session:
        assert session.scalar(
            select(func.count(UserAccountInvitation.id))
        ) == 2
        artifact = session.scalar(select(UserAccountMigrationArtifact))
        assert [
            item["databaseSnapshot"]
            for item in artifact.contents["backup"]["receipts"]
        ] == [
            "restic:production-20260729T120000Z",
            "restic:partial-migration-resume",
        ]


def test_apply_refuses_without_a_current_verified_backup(
    migration_store,
    tmp_path,
):
    factory, _ = migration_store
    provider = FakeIdentityProvider()
    source = mapping_csv(
        tmp_path / "unused.csv",
        [("missing", "new@example.com", "independent")],
    )
    invalid_receipt = tmp_path / "invalid-backup.json"
    invalid_receipt.write_text("{}")

    with pytest.raises(MigrationRefused, match="missing"):
        apply_user_account_migration(
            factory,
            provider,
            settings(tmp_path),
            source,
            approved_plan_checksum="a" * 64,
            backup_receipt_path=invalid_receipt,
            artifact_dir=tmp_path / "artifacts",
            operator="Operator",
            reason="Reason",
            confirmation=APPLY_CONFIRMATION,
        )
    with factory() as session:
        assert session.scalar(
            select(func.count(UserAccountMigrationRun.id))
        ) == 0
    assert provider.invite_calls == 0


def test_cli_dry_run_and_apply_use_the_external_command_contract(
    migration_store,
    tmp_path,
    monkeypatch,
):
    factory, _ = migration_store
    with factory.begin() as session:
        agency = Agency(slug="summit", name="Summit")
        session.add(agency)
    seed_customer(
        factory,
        slug="cli-customer",
        name="CLI Customer",
        agency=agency,
        old_email="old-cli@example.com",
        phone="2155550104",
    )
    source = mapping_csv(
        tmp_path / "cli.csv",
        [("cli-customer", "new-cli@example.com", "summit")],
    )
    provider = FakeIdentityProvider()
    monkeypatch.setattr("jawnix_data.cli.SessionLocal", factory)
    monkeypatch.setattr(
        "jawnix_data.cli.SupabaseMigrationIdentityProvider",
        lambda _settings: provider,
    )
    monkeypatch.setattr(
        "jawnix_data.cli.get_settings",
        lambda: settings(tmp_path),
    )

    runner = CliRunner()
    outcome = runner.invoke(
        app,
        ["user-account-migration-dry-run", str(source)],
    )

    assert outcome.exit_code == 0, outcome.output
    report = json.loads(outcome.stdout)
    assert report["summary"]["valid"] is True
    assert report["mutationPerformed"] is False

    receipt = backup_receipt(tmp_path / "cli-backup.json")
    applied = runner.invoke(
        app,
        [
            "user-account-migration-apply",
            str(source),
            "--approved-plan-sha256",
            report["planChecksum"],
            "--verified-backup",
            str(receipt),
            "--artifact-dir",
            str(tmp_path / "cli-artifacts"),
            "--operator",
            "CLI Operator",
            "--reason",
            "Approved CLI migration rehearsal",
            "--confirm",
            APPLY_CONFIRMATION,
        ],
    )
    assert applied.exit_code == 0, applied.output
    assert json.loads(applied.stdout)["status"] == "completed"
    assert provider.invite_calls == 1


def test_rollback_rehearsal_discards_the_mutated_copy(
    migration_store,
    tmp_path,
):
    factory, database = migration_store
    with factory.begin() as session:
        agency = Agency(slug="summit", name="Summit")
        session.add(agency)
    customer_id, old_id, event_id = seed_customer(
        factory,
        slug="rehearsal",
        name="Rehearsal",
        agency=agency,
        old_email="old-rehearsal@example.com",
        phone="2155550105",
    )
    rollback_copy = tmp_path / "verified-restore.db"
    shutil.copy2(database, rollback_copy)
    source = mapping_csv(
        tmp_path / "rehearsal.csv",
        [("rehearsal", "new-rehearsal@example.com", "summit")],
    )
    provider = FakeIdentityProvider()
    with factory() as session:
        plan = dry_run_user_account_migration(session, provider, source)
    apply(factory, provider, tmp_path, source, plan)

    restored_engine = create_engine(f"sqlite:///{rollback_copy}")
    restored_factory = sessionmaker(
        bind=restored_engine,
        expire_on_commit=False,
    )
    with restored_factory() as restored:
        assert restored.get(Agent, customer_id).id == customer_id
        assert restored.get(DistributionEvent, event_id).id == event_id
        assert restored.get(UserAccount, old_id).active is True
        assert restored.scalar(
            select(func.count(UserAccountMigrationRun.id))
        ) == 0
    restored_engine.dispose()
