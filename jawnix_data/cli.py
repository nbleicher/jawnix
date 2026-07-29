from __future__ import annotations

import json
import asyncio
import time
import uuid
from pathlib import Path

import typer
from sqlalchemy import func, select

from jawnix.allocation import allocate_request
from jawnix.config import get_settings
from jawnix.database import SessionLocal
from jawnix.delivery import deliver_request, mark_delivery_failed
from jawnix.maintenance import expire_batch_files
from jawnix.break_glass import BreakGlassRequest, perform_break_glass
from jawnix.mfa_provider import get_mfa_provider
from jawnix.models import Agent, CustomerProfile, DistributionEvent, Lead, LeadRequest, RequestStatus
from jawnix.states import normalize_states

from .migration import import_agent_config, import_distribution_history, import_manifest, import_scraper_sqlite, import_supabase_jsonl
from .scraper import sync_scraper
from .restore import validate_or_replay_restored_dataset
from .configuration import prepare_agent_config
from .customer_mappings import provision_customer_mappings
from .user_account_migration import (
    MigrationRefused,
    SupabaseMigrationIdentityProvider,
    apply_user_account_migration,
    dry_run_user_account_migration,
    write_mapping_draft,
)


app = typer.Typer(no_args_is_help=True, help="Jawnix data migration and batch operations")


def emit(value: object) -> None:
    typer.echo(json.dumps(value, indent=2, default=str))


@app.command("admin-mfa-break-glass")
def admin_mfa_break_glass(
    target_user_id: uuid.UUID = typer.Option(..., "--target-user-id"),
    target_email: str = typer.Option(..., "--target-email"),
    operator: str = typer.Option(..., "--operator"),
    authorizer: str = typer.Option(..., "--authorizer"),
    reason: str = typer.Option(..., "--reason"),
    reference: str = typer.Option(..., "--reference"),
    confirm: str = typer.Option(..., "--confirm"),
):
    """Revoke a locked-out administrator and require two-factor reenrollment."""

    if confirm != "REVOKE-AND-REENROLL":
        raise typer.BadParameter(
            "confirmation must be exactly REVOKE-AND-REENROLL",
            param_hint="--confirm",
        )
    request = BreakGlassRequest(
        target_user_id=target_user_id,
        target_email=target_email,
        operator=operator,
        authorizer=authorizer,
        reason=reason,
        reference=reference,
    )
    try:
        request.validate()
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from None
    settings = get_settings()
    with SessionLocal() as session:
        result = asyncio.run(
            perform_break_glass(
                session,
                get_mfa_provider(settings),
                request,
            )
        )
    emit(result)


@app.command("import-config")
def import_config(path: Path):
    with SessionLocal.begin() as session:
        emit(import_agent_config(session, path))


@app.command("prepare-config")
def prepare_config(
    source: Path,
    destination: Path,
    overrides: Path = typer.Option(Path("config/migration-overrides.json"), "--overrides"),
):
    emit(prepare_agent_config(source, destination, overrides))


@app.command("provision-customer-mappings")
def provision_mappings(
    path: Path = typer.Argument(Path("config/customer-agent-mappings.csv")),
    invite_missing: bool = typer.Option(False, "--invite-missing"),
):
    with SessionLocal.begin() as session:
        emit(provision_customer_mappings(session, get_settings(), path, invite_missing=invite_missing))


@app.command("user-account-migration-draft")
def user_account_migration_draft(
    path: Path = typer.Argument(Path("config/user-account-migration.csv")),
):
    """Write a mapping draft from live data, with only `email` left to fill.

    Reads every active Customer and its current Agency, so the selectors are
    guaranteed to resolve. `migrate` is written empty: rows are opt-in, so an
    untouched draft migrates nobody.
    """

    with SessionLocal() as session:
        emit(write_mapping_draft(session, path))


@app.command("user-account-migration-dry-run")
def user_account_migration_dry_run(
    path: Path = typer.Argument(...),
):
    """Preflight every identity and history mapping without mutation."""

    settings = get_settings()
    try:
        with SessionLocal() as session:
            emit(
                dry_run_user_account_migration(
                    session,
                    SupabaseMigrationIdentityProvider(settings),
                    path,
                )
            )
    except MigrationRefused as exc:
        raise typer.BadParameter(str(exc)) from None


@app.command("user-account-migration-apply")
def user_account_migration_apply(
    path: Path = typer.Argument(...),
    approved_plan_sha256: str = typer.Option(
        ...,
        "--approved-plan-sha256",
    ),
    verified_backup: Path = typer.Option(..., "--verified-backup"),
    artifact_dir: Path = typer.Option(
        Path("/srv/jawnix/migration/reconciliation"),
        "--artifact-dir",
    ),
    operator: str = typer.Option(..., "--operator"),
    reason: str = typer.Option(..., "--reason"),
    confirm: str = typer.Option(..., "--confirm"),
):
    """Apply an approved plan behind the verified-backup refusal gate."""

    settings = get_settings()
    try:
        emit(
            apply_user_account_migration(
                SessionLocal,
                SupabaseMigrationIdentityProvider(settings),
                settings,
                path,
                approved_plan_checksum=approved_plan_sha256,
                backup_receipt_path=verified_backup,
                artifact_dir=artifact_dir,
                operator=operator,
                reason=reason,
                confirmation=confirm,
            )
        )
    except MigrationRefused as exc:
        raise typer.BadParameter(str(exc)) from None


@app.command("import-manifest")
def import_manifest_command(path: Path, expected_sha256: str = ""):
    with SessionLocal.begin() as session:
        emit(import_manifest(session, path, expected_sha256 or None))


@app.command("import-scraper-db")
def import_scraper_command(path: Path, expected_sha256: str = ""):
    with SessionLocal.begin() as session:
        emit(import_scraper_sqlite(session, path, expected_sha256 or None))


@app.command("import-supabase")
def import_supabase_command(directory: Path):
    with SessionLocal.begin() as session:
        emit(import_supabase_jsonl(session, directory))


@app.command("sync-scrapers")
def sync_scrapers(source: str = "", force: bool = False):
    with SessionLocal.begin() as session:
        emit(sync_scraper(session, get_settings(), source or None, force))


@app.command("restore-scraper-dataset")
def restore_scraper_dataset(
    dataset: Path = typer.Option(..., "--dataset"),
    metadata: Path = typer.Option(..., "--metadata"),
    apply: bool = typer.Option(False, "--apply"),
):
    with SessionLocal.begin() as session:
        emit(
            validate_or_replay_restored_dataset(
                session,
                get_settings(),
                dataset,
                metadata,
                apply=apply,
            )
        )


@app.command("import-history")
def import_history(directory: Path):
    with SessionLocal.begin() as session:
        emit(import_distribution_history(session, directory))


@app.command("redistribute")
def redistribute(
    request_id: uuid.UUID = typer.Option(..., "--request-id"),
    deliver: bool = True,
):
    settings = get_settings()
    with SessionLocal.begin() as session:
        item = session.get(LeadRequest, request_id)
        if item is None:
            raise typer.BadParameter("request was not found")
        if item.status in {RequestStatus.pending.value, RequestStatus.waiting_inventory.value}:
            item.status = RequestStatus.approved.value
        result = allocate_request(session, request_id, settings)
        emit(result.__dict__)
    if deliver and result.allocated:
        try:
            with SessionLocal.begin() as session:
                emit({"resendMessageId": deliver_request(session, request_id, settings)})
        except Exception as exc:
            with SessionLocal.begin() as session:
                mark_delivery_failed(session, request_id, str(exc))
            raise


@app.command("retry-delivery")
def retry_delivery(request_id: uuid.UUID = typer.Option(..., "--request-id")):
    try:
        with SessionLocal.begin() as session:
            emit({"resendMessageId": deliver_request(session, request_id, get_settings())})
    except Exception as exc:
        with SessionLocal.begin() as session:
            mark_delivery_failed(session, request_id, str(exc))
        raise


@app.command("inventory")
def inventory(states: str = ""):
    selected = normalize_states([value for value in states.split(",") if value]) if states else []
    with SessionLocal() as session:
        query = select(Lead.state, func.count(Lead.id)).group_by(Lead.state).order_by(Lead.state)
        if selected:
            query = query.where(Lead.state.in_(selected))
        emit({state: count for state, count in session.execute(query)})


@app.command("dry-run-allocation")
def dry_run_allocation(
    agent_slug: str = typer.Option("noah", "--agent"),
    lead_count: int = typer.Option(100_000, "--count", min=1, max=100_000),
    states: str = typer.Option("TX", "--states"),
):
    """Exercise allocation and CSV generation, then roll back every database write."""
    selected = normalize_states([value for value in states.split(",") if value])
    if not selected:
        raise typer.BadParameter("at least one state is required")
    artifact_path: Path | None = None
    session = SessionLocal()
    started = time.perf_counter()
    try:
        agent = session.scalar(select(Agent).where(Agent.slug == agent_slug, Agent.active.is_(True)))
        if agent is None:
            raise typer.BadParameter(f"agent was not found or is inactive: {agent_slug}")
        profile = session.scalar(
            select(CustomerProfile)
            .where(
                CustomerProfile.agent_id == agent.id,
                CustomerProfile.mapping_confirmed_at.is_not(None),
            )
            .limit(1)
        )
        if profile is None:
            raise typer.BadParameter(f"agent has no confirmed customer profile: {agent_slug}")
        request = LeadRequest(
            user_id=profile.user_id,
            agent_id=agent.id,
            lead_count=lead_count,
            state_mode="selected",
            states_snapshot=selected,
            delivery_email=profile.email,
            status=RequestStatus.approved.value,
            status_message="Rollback-only allocation dry run.",
        )
        session.add(request)
        session.flush()
        result = allocate_request(session, request.id, get_settings())
        if result.allocated != lead_count:
            raise RuntimeError(
                f"dry run allocated {result.allocated:,} of {lead_count:,} requested rows"
            )
        artifact = request.artifact
        if artifact is None or artifact.row_count != lead_count:
            raise RuntimeError("dry-run artifact row count did not match the allocation")
        artifact_path = Path(artifact.path)
        event_count = int(
            session.scalar(
                select(func.count(DistributionEvent.id)).where(
                    DistributionEvent.request_id == request.id
                )
            )
            or 0
        )
        if event_count != lead_count:
            raise RuntimeError("dry-run event count did not match the allocation")
        elapsed = time.perf_counter() - started
        output = {
            "rolledBack": True,
            "agent": agent_slug,
            "states": selected,
            "requested": lead_count,
            "allocated": result.allocated,
            "events": event_count,
            "csvRows": artifact.row_count,
            "csvBytes": artifact.byte_count,
            "csvSha256": artifact.sha256,
            "elapsedSeconds": round(elapsed, 3),
            "underFiveMinutes": elapsed < 300,
        }
        session.rollback()
        emit(output)
    finally:
        session.rollback()
        session.close()
        if artifact_path is not None:
            artifact_path.unlink(missing_ok=True)


@app.command("expire-artifacts")
def expire_artifacts():
    with SessionLocal.begin() as session:
        emit(expire_batch_files(session, get_settings()))


if __name__ == "__main__":
    app()
