from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


ID_TYPE = BigInteger().with_variant(Integer, "sqlite")


class RequestStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    processing = "processing"
    waiting_inventory = "waiting_inventory"
    generated = "generated"
    delivered = "delivered"
    rejected = "rejected"
    canceled = "canceled"
    failed = "failed"


class JobStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    complete = "complete"
    failed = "failed"


class Agency(Base):
    __tablename__ = "agencies"
    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_fulfilled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        index=True,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Agent(Base):
    __tablename__ = "agents"
    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    agency_id: Mapped[int | None] = mapped_column(ForeignKey("agencies.id", ondelete="SET NULL"), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_fulfilled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        index=True,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    agency: Mapped[Agency | None] = relationship()


class CustomerProfile(Base):
    __tablename__ = "customer_profiles"
    user_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    first_name: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    last_name: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    phone: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    licensed_states: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id", ondelete="SET NULL"), index=True)
    mapping_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    agent: Mapped[Agent | None] = relationship()

    @property
    def customer_id(self) -> int | None:
        return self.agent_id


class LeadRequest(Base):
    __tablename__ = "lead_requests"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customer_profiles.user_id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="RESTRICT"), index=True)
    lead_count: Mapped[int] = mapped_column(Integer, nullable=False)
    state_mode: Mapped[str] = mapped_column(String(20), default="all_saved", nullable=False)
    states_snapshot: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    delivery_email: Mapped[str] = mapped_column(String(320), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default=RequestStatus.pending.value, index=True, nullable=False)
    available_count: Mapped[int | None] = mapped_column(Integer)
    status_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True, nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    profile: Mapped[CustomerProfile] = relationship()
    agent: Mapped[Agent] = relationship()
    artifact: Mapped[BatchArtifact | None] = relationship(back_populates="request", uselist=False)


class Lead(Base):
    __tablename__ = "lead_inventory"
    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    phone: Mapped[str] = mapped_column(String(10), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(Text, default="", nullable=False)
    state: Mapped[str] = mapped_column(String(2), index=True, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    source_flow: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    legacy_title: Mapped[str] = mapped_column(Text, default="", nullable=False)
    legacy_state: Mapped[str] = mapped_column(String(2), default="", nullable=False)
    current_listing_observation_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "listing_observations.id",
            ondelete="SET NULL",
            name="fk_lead_current_listing_observation",
        ),
        index=True,
    )
    last_distributed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    __table_args__ = (Index("lead_inventory_state_age_idx", "state", "last_distributed_at", "id"),)


class LeadSource(Base):
    __tablename__ = "lead_sources"
    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("lead_inventory.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    source_key: Mapped[str] = mapped_column(String(200), nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    __table_args__ = (UniqueConstraint("source", "source_key", name="uq_lead_source_key"),)


class ListingObservation(Base):
    __tablename__ = "listing_observations"
    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    lead_id: Mapped[int | None] = mapped_column(
        ForeignKey("lead_inventory.id", ondelete="SET NULL"),
        index=True,
    )
    dataset_checksum: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    row_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    normalized_phone: Mapped[str] = mapped_column(String(10), default="", nullable=False)
    title: Mapped[str] = mapped_column(Text, default="", nullable=False)
    state: Mapped[str] = mapped_column(String(2), default="", nullable=False)
    source: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    niche: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    valid: Mapped[bool] = mapped_column(Boolean, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    __table_args__ = (
        UniqueConstraint(
            "dataset_checksum",
            "row_number",
            name="uq_listing_observation_dataset_row",
        ),
        Index(
            "listing_observation_lead_recency_idx",
            "lead_id",
            "valid",
            "observed_at",
            "row_number",
        ),
    )


class DistributionEvent(Base):
    __tablename__ = "distribution_events"
    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("lead_inventory.id", ondelete="RESTRICT"), index=True)
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id", ondelete="SET NULL"), index=True)
    customer_name: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    agency_id: Mapped[int | None] = mapped_column(ForeignKey("agencies.id", ondelete="RESTRICT"), index=True)
    agency_name: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    request_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("lead_requests.id", ondelete="SET NULL"), index=True)
    phone: Mapped[str] = mapped_column(String(10), default="", nullable=False)
    title: Mapped[str] = mapped_column(Text, default="", nullable=False)
    state: Mapped[str] = mapped_column(String(2), default="", nullable=False)
    listing_provenance: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), default="legacy", index=True, nullable=False)
    source_segment_key: Mapped[str] = mapped_column(String(320), default="", index=True, nullable=False)
    source_niche: Mapped[str] = mapped_column(String(160), default="", index=True, nullable=False)
    delivered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True, nullable=False)
    source: Mapped[str] = mapped_column(String(80), default="request", nullable=False)
    __table_args__ = (
        UniqueConstraint("request_id", "lead_id", name="uq_request_lead"),
        UniqueConstraint("lead_id", "agent_id", "delivered_at", "source", name="uq_legacy_distribution_event"),
    )

    @property
    def customer_id(self) -> int | None:
        """Canonical domain name for the legacy-compatible agent_id column."""
        return self.agent_id


class LeadOutcome(Base):
    __tablename__ = "lead_outcomes"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    distribution_event_id: Mapped[int] = mapped_column(
        ForeignKey("distribution_events.id", ondelete="RESTRICT"),
        index=True,
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("agents.id", ondelete="RESTRICT"),
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    metric: Mapped[str] = mapped_column(String(40), nullable=False)
    appointment_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    supersedes_outcome_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("lead_outcomes.id", ondelete="RESTRICT"),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        index=True,
        nullable=False,
    )


class BatchArtifact(Base):
    __tablename__ = "batch_artifacts"
    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("lead_requests.id", ondelete="CASCADE"), unique=True)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    byte_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    delivery_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    delivery_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    resend_message_id: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    last_error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    request: Mapped[LeadRequest] = relationship(back_populates="artifact")


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(60), index=True, nullable=False)
    request_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("lead_requests.id", ondelete="CASCADE"), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=JobStatus.queued.value, index=True, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True, nullable=False)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    last_error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("lead_requests.id", ondelete="CASCADE"), unique=True)
    provider: Mapped[str] = mapped_column(String(40), default="telegram", nullable=False)
    destination_id: Mapped[str] = mapped_column(String(120), nullable=False)
    message_id: Mapped[str] = mapped_column(String(120), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class WebhookReceipt(Base):
    __tablename__ = "webhook_receipts"
    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    event_key: Mapped[str] = mapped_column(String(160), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    __table_args__ = (UniqueConstraint("provider", "event_key", name="uq_webhook_receipt"),)


class ScraperRun(Base):
    __tablename__ = "scraper_runs"
    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    source_version: Mapped[str] = mapped_column(String(255), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    rows_seen: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    rows_imported: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NightlyReview(Base):
    __tablename__ = "nightly_reviews"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    scraper_run_id: Mapped[int] = mapped_column(
        ForeignKey("scraper_runs.id", ondelete="RESTRICT"),
        unique=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(24),
        default="complete",
        index=True,
        nullable=False,
    )
    summary: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    telegram_message_id: Mapped[str] = mapped_column(
        String(120),
        default="",
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        index=True,
        nullable=False,
    )


class MigrationAudit(Base):
    __tablename__ = "migration_audits"
    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    source_rows: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    imported_rows: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    quarantined_rows: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    __table_args__ = (UniqueConstraint("source_path", "checksum", name="uq_migration_source_checksum"),)


class QuarantinedRow(Base):
    __tablename__ = "quarantined_rows"
    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    row_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


# Expand/contract compatibility: new code and APIs use Customer while the
# existing production table/foreign keys retain their stable names.
Customer = Agent
