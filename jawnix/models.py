from __future__ import annotations

import enum
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, text
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
    licensed_states: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    agency_id: Mapped[int | None] = mapped_column(ForeignKey("agencies.id", ondelete="SET NULL"), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_fulfilled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        index=True,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    agency: Mapped[Agency | None] = relationship()


# Canonical domain type. The legacy class/table name remains only as an
# expand/contract persistence compatibility detail.
Customer = Agent


class UserAccount(Base):
    __tablename__ = "user_accounts"
    auth_user_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("agents.id", ondelete="RESTRICT"),
        index=True,
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        index=True,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )
    replaced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    replaced_by_auth_user_id: Mapped[uuid.UUID | None] = mapped_column()
    customer: Mapped[Customer | None] = relationship()

    __table_args__ = (
        Index(
            "uq_active_user_account_per_customer",
            "customer_id",
            unique=True,
            postgresql_where=text("active"),
            sqlite_where=text("active = 1"),
        ),
    )


class UserAccountInvitation(Base):
    """An outstanding offer of access to one durable Customer.

    Provisioning is invitation-only: an administrator names the email and the
    provider owns the credential, so no password ever passes through Jawnix.
    The invitation is the reason a replacement can be prepared without
    disturbing anything -- the Customer keeps its identity, its history, and
    its currently active User Account until the invited person accepts.

    ``pending`` is therefore the only state the constraints care about. The
    partial unique index below permits at most one outstanding invitation per
    Customer, which is what makes acceptance a safe atomic swap: the winner of
    that index is the only account that can ever be promoted.
    """

    __tablename__ = "user_account_invitations"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("agents.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    auth_user_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        default="pending",
        nullable=False,
    )
    # The account this invitation will retire on acceptance. Null on first
    # provisioning, where there is nothing to replace.
    replaces_auth_user_id: Mapped[uuid.UUID | None] = mapped_column()
    invited_by: Mapped[str] = mapped_column(String(160), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    canceled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    customer: Mapped[Customer] = relationship()

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'accepted', 'canceled')",
            name="ck_user_account_invitations_status",
        ),
        Index(
            "uq_pending_user_account_invitation_per_customer",
            "customer_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
            sqlite_where=text("status = 'pending'"),
        ),
        # An identity can be invited again after a cancellation, so uniqueness
        # applies to outstanding offers rather than to the whole history.
        Index(
            "uq_pending_user_account_invitation_per_identity",
            "auth_user_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
            sqlite_where=text("status = 'pending'"),
        ),
    )


class AdminMFAState(Base):
    """Jawnix-owned state around provider-managed administrator factors.

    Supabase owns every TOTP secret and challenge.  Jawnix stores only the
    coordination state that Supabase cannot express: which factors pre-dated a
    resumable enrollment, challenge throttling, and a generation used to revoke
    all signed Jawnix sessions after a security change.
    """

    __tablename__ = "admin_mfa_states"
    user_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    session_generation: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
    )
    enrollment_stage: Mapped[str] = mapped_column(
        String(32),
        default="idle",
        index=True,
        nullable=False,
    )
    enrollment_baseline_factor_ids: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    enrollment_new_factor_ids: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    active_factor_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    replacement_factor_id: Mapped[uuid.UUID | None] = mapped_column()
    failed_attempts: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    failure_window_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )


class AdminMFAFactorUse(Base):
    """Last successful use location for a provider-managed factor."""

    __tablename__ = "admin_mfa_factor_uses"
    provider_factor_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        index=True,
        nullable=False,
    )
    ip_address: Mapped[str] = mapped_column(
        String(80),
        default="unknown",
        nullable=False,
    )
    user_agent: Mapped[str] = mapped_column(
        String(320),
        default="unknown",
        nullable=False,
    )


class CustomerTombstone(Base):
    __tablename__ = "customer_tombstones"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    former_customer_id: Mapped[int] = mapped_column(
        BigInteger,
        unique=True,
        nullable=False,
    )
    opaque_key: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
    )
    actor_id: Mapped[str] = mapped_column(String(160), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    erased_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )


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

    @customer_id.setter
    def customer_id(self, value: int | None) -> None:
        self.agent_id = value

    @property
    def customer(self) -> Customer | None:
        return self.agent

    @customer.setter
    def customer(self, value: Customer | None) -> None:
        self.agent = value


class LeadRequest(Base):
    __tablename__ = "lead_requests"
    __table_args__ = (
        # One Batch Request per submission key. A retried or double-clicked
        # submission therefore cannot become a second request even when two
        # attempts reach the database concurrently. A unique index rather than
        # a constraint so the same DDL applies on SQLite and PostgreSQL; NULL
        # keys stay distinct under both, leaving pre-existing rows valid.
        Index(
            "uq_lead_request_idempotency",
            "user_id",
            "idempotency_key",
            unique=True,
        ),
    )
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
    # When the request stopped short of delivery. It timestamps the rejected,
    # canceled, or failed node of the Customer milestone graph, and a retry
    # clears it because the request is moving again.
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Supplied by the Customer application, once per guided submission.
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    profile: Mapped[CustomerProfile] = relationship()
    agent: Mapped[Agent] = relationship()
    artifact: Mapped[BatchArtifact | None] = relationship(back_populates="request", uselist=False)

    @property
    def customer_id(self) -> int:
        return self.agent_id

    @customer_id.setter
    def customer_id(self, value: int) -> None:
        self.agent_id = value

    @property
    def customer(self) -> Customer:
        return self.agent

    @customer.setter
    def customer(self, value: Customer) -> None:
        self.agent = value


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
    active_correction_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "lead_correction_events.id",
            ondelete="SET NULL",
            name="fk_lead_active_correction",
        ),
        index=True,
    )
    suppressed: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        index=True,
        nullable=False,
    )
    suppression_reason: Mapped[str] = mapped_column(
        Text,
        default="",
        nullable=False,
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


class LeadCorrectionEvent(Base):
    __tablename__ = "lead_correction_events"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    lead_id: Mapped[int] = mapped_column(
        ForeignKey("lead_inventory.id", ondelete="RESTRICT"),
        index=True,
    )
    action: Mapped[str] = mapped_column(String(24), index=True, nullable=False)
    title: Mapped[str] = mapped_column(Text, default="", nullable=False)
    state: Mapped[str] = mapped_column(String(2), default="", nullable=False)
    actor_id: Mapped[str] = mapped_column(String(160), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    supersedes_correction_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("lead_correction_events.id", ondelete="RESTRICT"),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        index=True,
        nullable=False,
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
    distribution_period: Mapped[str] = mapped_column(
        String(7),
        default="",
        index=True,
        nullable=False,
    )
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

    @customer_id.setter
    def customer_id(self, value: int | None) -> None:
        self.agent_id = value


class LeadDispositionTransition(Base):
    __tablename__ = "lead_disposition_transitions"
    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    distribution_event_id: Mapped[int] = mapped_column(
        ForeignKey("distribution_events.id", ondelete="RESTRICT"),
        index=True,
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("agents.id", ondelete="RESTRICT"),
        index=True,
    )
    actor_user_id: Mapped[uuid.UUID] = mapped_column(index=True)
    source_outcome_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "lead_outcomes.id",
            ondelete="RESTRICT",
            name="fk_disposition_transition_source_outcome",
            use_alter=True,
        ),
        unique=True,
        index=True,
    )
    disposition: Mapped[str] = mapped_column(
        String(40),
        index=True,
        nullable=False,
    )
    note: Mapped[str] = mapped_column(
        Text,
        default="",
        nullable=False,
    )
    previous_transition_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "lead_disposition_transitions.id",
            ondelete="RESTRICT",
        ),
        unique=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        index=True,
        nullable=False,
    )
    __table_args__ = (
        CheckConstraint(
            (
                "disposition IN ('no_contact', 'not_interested', "
                "'positive_response', 'appointment_booked', "
                "'appointment_canceled', 'appointment_no_show', "
                "'invalid_phone', 'wrong_business', "
                "'do_not_contact', 'other')"
            ),
            name="ck_lead_disposition_transition_value",
        ),
    )


class LeadDispositionState(Base):
    __tablename__ = "lead_disposition_states"
    distribution_event_id: Mapped[int] = mapped_column(
        ForeignKey("distribution_events.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    current_transition_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "lead_disposition_transitions.id",
            ondelete="RESTRICT",
        ),
        unique=True,
        nullable=False,
    )
    current_disposition: Mapped[str] = mapped_column(
        String(40),
        index=True,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


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
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
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


class LeadReport(Base):
    __tablename__ = "lead_reports"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    distribution_event_id: Mapped[int] = mapped_column(
        ForeignKey("distribution_events.id", ondelete="RESTRICT"),
        index=True,
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("agents.id", ondelete="RESTRICT"),
        index=True,
    )
    source_transition_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "lead_disposition_transitions.id",
            ondelete="RESTRICT",
        ),
        unique=True,
        index=True,
    )
    reason: Mapped[str] = mapped_column(
        String(40),
        index=True,
        nullable=False,
    )
    details: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(
        String(24),
        default="open",
        index=True,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        index=True,
        nullable=False,
    )


class LeadReportResolution(Base):
    __tablename__ = "lead_report_resolutions"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    report_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("lead_reports.id", ondelete="RESTRICT"),
        unique=True,
        index=True,
    )
    action: Mapped[str] = mapped_column(
        String(24),
        index=True,
        nullable=False,
    )
    note: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )


class EligibilityHold(Base):
    __tablename__ = "eligibility_holds"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    lead_id: Mapped[int] = mapped_column(
        ForeignKey("lead_inventory.id", ondelete="RESTRICT"),
        index=True,
    )
    distribution_event_id: Mapped[int] = mapped_column(
        ForeignKey("distribution_events.id", ondelete="RESTRICT"),
        index=True,
    )
    report_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("lead_reports.id", ondelete="RESTRICT"),
        unique=True,
        index=True,
    )
    reason: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        index=True,
        nullable=False,
    )
    released_by: Mapped[str] = mapped_column(
        String(160),
        default="",
        nullable=False,
    )
    release_reason: Mapped[str] = mapped_column(
        Text,
        default="",
        nullable=False,
    )
    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
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
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        index=True,
    )
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
    configuration_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "scraper_configurations.id",
            ondelete="RESTRICT",
            name="fk_scraper_run_configuration",
        ),
        index=True,
    )
    dataset_version: Mapped[int | None] = mapped_column(
        BigInteger,
        index=True,
    )
    staged_path: Mapped[str] = mapped_column(Text, default="", nullable=False)
    manual: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    checksum: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    rows_seen: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    rows_imported: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ScraperConfiguration(Base):
    __tablename__ = "scraper_configurations"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    version: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    status: Mapped[str] = mapped_column(
        String(24),
        default="draft",
        index=True,
        nullable=False,
    )
    anomaly_thresholds: Mapped[dict] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    created_by: Mapped[uuid.UUID] = mapped_column(nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        index=True,
        nullable=False,
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    based_on_configuration_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("scraper_configurations.id", ondelete="RESTRICT"),
        index=True,
    )
    segments: Mapped[list[SourceSegment]] = relationship(
        back_populates="configuration",
        order_by="SourceSegment.key",
        cascade="all, delete-orphan",
    )


class SourceSegment(Base):
    __tablename__ = "source_segments"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    configuration_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scraper_configurations.id", ondelete="RESTRICT"),
        index=True,
    )
    key: Mapped[str] = mapped_column(String(160), nullable=False)
    niche: Mapped[str] = mapped_column(String(160), nullable=False)
    query: Mapped[str] = mapped_column(String(320), nullable=False)
    geography: Mapped[str] = mapped_column(String(320), nullable=False)
    parameters: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    configuration: Mapped[ScraperConfiguration] = relationship(
        back_populates="segments"
    )
    __table_args__ = (
        UniqueConstraint(
            "configuration_id",
            "key",
            name="uq_source_segment_configuration_key",
        ),
    )


class SourceNicheMapping(Base):
    __tablename__ = "source_niche_mappings"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    segment_key: Mapped[str] = mapped_column(
        String(320),
        unique=True,
        index=True,
        nullable=False,
    )
    state: Mapped[str] = mapped_column(String(2), index=True, nullable=False)
    keyword: Mapped[str] = mapped_column(
        String(240),
        index=True,
        nullable=False,
    )
    niche: Mapped[str] = mapped_column(
        String(160),
        index=True,
        nullable=False,
    )
    confirmed: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        index=True,
        nullable=False,
    )
    proposal_source: Mapped[str] = mapped_column(
        String(40),
        default="migration",
        nullable=False,
    )
    proposed_evidence: Mapped[dict] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    confirmed_by: Mapped[str] = mapped_column(
        String(160),
        default="",
        nullable=False,
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )


class DailySourcePerformance(Base):
    __tablename__ = "daily_source_performance"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    nightly_review_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("nightly_reviews.id", ondelete="RESTRICT"),
        index=True,
    )
    snapshot_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    segment_key: Mapped[str] = mapped_column(
        String(320),
        index=True,
        nullable=False,
    )
    state: Mapped[str] = mapped_column(String(2), index=True, nullable=False)
    keyword: Mapped[str] = mapped_column(
        String(240),
        index=True,
        nullable=False,
    )
    niche: Mapped[str] = mapped_column(
        String(160),
        index=True,
        nullable=False,
    )
    niche_confirmed: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        index=True,
        nullable=False,
    )
    window_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    window_ended_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    counts: Mapped[dict] = mapped_column(JSON, nullable=False)
    rates: Mapped[dict] = mapped_column(JSON, nullable=False)
    intervals: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    trend: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    eligibility: Mapped[str] = mapped_column(
        String(48),
        index=True,
        nullable=False,
    )
    action_state: Mapped[str] = mapped_column(
        String(32),
        default="notes_only",
        index=True,
        nullable=False,
    )
    evidence_checksum: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )
    __table_args__ = (
        UniqueConstraint(
            "snapshot_date",
            "segment_key",
            name="uq_daily_source_performance_date_segment",
        ),
    )


class PerformanceSuggestionNote(Base):
    __tablename__ = "performance_suggestion_notes"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("daily_source_performance.id", ondelete="RESTRICT"),
        unique=True,
        index=True,
    )
    segment_key: Mapped[str] = mapped_column(
        String(320),
        index=True,
        nullable=False,
    )
    template_key: Mapped[str] = mapped_column(String(80), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False)
    evidence_checksum: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )


class DatasetPublication(Base):
    __tablename__ = "dataset_publications"
    id: Mapped[int] = mapped_column(
        ID_TYPE,
        primary_key=True,
        autoincrement=True,
    )
    version: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    scraper_run_id: Mapped[int] = mapped_column(
        ForeignKey("scraper_runs.id", ondelete="RESTRICT"),
        unique=True,
        index=True,
    )
    configuration_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scraper_configurations.id", ondelete="RESTRICT"),
        index=True,
    )
    storage_path: Mapped[str] = mapped_column(Text, default="", nullable=False)
    sync_status: Mapped[str] = mapped_column(
        String(24),
        default="pending",
        index=True,
        nullable=False,
    )
    synchronized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    committed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        index=True,
        nullable=False,
    )


class InventorySyncAttempt(Base):
    __tablename__ = "inventory_sync_attempts"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    dataset_publication_id: Mapped[int] = mapped_column(
        ForeignKey("dataset_publications.id", ondelete="RESTRICT"),
        index=True,
    )
    dataset_version: Mapped[int] = mapped_column(
        BigInteger,
        index=True,
        nullable=False,
    )
    dataset_checksum: Mapped[str] = mapped_column(
        String(64),
        index=True,
        nullable=False,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(24),
        index=True,
        nullable=False,
    )
    result: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    __table_args__ = (
        UniqueConstraint(
            "dataset_publication_id",
            "attempt_number",
            name="uq_inventory_sync_publication_attempt",
        ),
    )


class ScrapeSegmentResult(Base):
    __tablename__ = "scrape_segment_results"
    id: Mapped[int] = mapped_column(
        ID_TYPE,
        primary_key=True,
        autoincrement=True,
    )
    scraper_run_id: Mapped[int] = mapped_column(
        ForeignKey("scraper_runs.id", ondelete="RESTRICT"),
        index=True,
    )
    segment_key: Mapped[str] = mapped_column(String(160), index=True, nullable=False)
    niche: Mapped[str] = mapped_column(String(160), index=True, nullable=False)
    geography: Mapped[str] = mapped_column(String(320), nullable=False)
    observed_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    valid_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    new_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    duplicate_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    quarantined_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    anomalous: Mapped[bool] = mapped_column(Boolean, index=True, nullable=False)
    anomaly_reasons: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    __table_args__ = (
        UniqueConstraint(
            "scraper_run_id",
            "segment_key",
            name="uq_scrape_run_segment_result",
        ),
    )


class ScrapeAnomaly(Base):
    __tablename__ = "scrape_anomalies"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    scraper_run_id: Mapped[int] = mapped_column(
        ForeignKey("scraper_runs.id", ondelete="RESTRICT"),
        unique=True,
        index=True,
    )
    configuration_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scraper_configurations.id", ondelete="RESTRICT"),
        index=True,
    )
    dataset_checksum: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    status: Mapped[str] = mapped_column(
        String(24),
        default="pending",
        index=True,
        nullable=False,
    )
    decision_by: Mapped[str] = mapped_column(
        String(160),
        default="",
        nullable=False,
    )
    decision_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    telegram_chat_id: Mapped[str] = mapped_column(
        String(120),
        default="",
        nullable=False,
    )
    telegram_message_id: Mapped[str] = mapped_column(
        String(120),
        default="",
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )


class InventoryConflict(Base):
    __tablename__ = "inventory_conflicts"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    older_request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("lead_requests.id", ondelete="RESTRICT"),
        index=True,
    )
    newer_request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("lead_requests.id", ondelete="RESTRICT"),
        index=True,
    )
    inventory_snapshot: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
    )
    snapshot_checksum: Mapped[str] = mapped_column(
        String(64),
        index=True,
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(24),
        default="pending",
        index=True,
        nullable=False,
    )
    decision_by: Mapped[str] = mapped_column(
        String(160),
        default="",
        nullable=False,
    )
    decision_reason: Mapped[str] = mapped_column(
        Text,
        default="",
        nullable=False,
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    telegram_chat_id: Mapped[str] = mapped_column(
        String(120),
        default="",
        nullable=False,
    )
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
    __table_args__ = (
        UniqueConstraint(
            "older_request_id",
            "newer_request_id",
            "snapshot_checksum",
            name="uq_inventory_conflict_scope",
        ),
    )


class SourceRecommendation(Base):
    __tablename__ = "source_recommendations"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    niche: Mapped[str] = mapped_column(
        String(160),
        index=True,
        nullable=False,
    )
    segment_key: Mapped[str] = mapped_column(
        String(320),
        index=True,
        nullable=False,
    )
    action: Mapped[str] = mapped_column(
        String(24),
        index=True,
        nullable=False,
    )
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False)
    evidence_checksum: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(24),
        default="pending",
        index=True,
        nullable=False,
    )
    decision_by: Mapped[str] = mapped_column(
        String(160),
        default="",
        nullable=False,
    )
    decision_reason: Mapped[str] = mapped_column(
        Text,
        default="",
        nullable=False,
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    resulting_configuration_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("scraper_configurations.id", ondelete="RESTRICT"),
        index=True,
    )
    nightly_review_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("nightly_reviews.id", ondelete="RESTRICT"),
        index=True,
    )
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("daily_source_performance.id", ondelete="RESTRICT"),
        index=True,
    )
    configuration_version: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        index=True,
        nullable=False,
    )


class NightlyReview(Base):
    __tablename__ = "nightly_reviews"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    scraper_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("scraper_runs.id", ondelete="RESTRICT"),
        unique=True,
        index=True,
    )
    review_date: Mapped[date | None] = mapped_column(
        Date,
        unique=True,
        index=True,
    )
    scheduled_for: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
    )
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
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
    telegram_delivery_state: Mapped[str] = mapped_column(
        String(24),
        default="pending",
        index=True,
        nullable=False,
    )
    telegram_delivery_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    telegram_delivery_error: Mapped[str] = mapped_column(
        Text,
        default="",
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        index=True,
        nullable=False,
    )


class AuditEntry(Base):
    __tablename__ = "audit_entries"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    action: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    target_type: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    target_id: Mapped[str] = mapped_column(String(160), index=True, nullable=False)
    actor_user_id: Mapped[str] = mapped_column(
        String(160),
        index=True,
        nullable=False,
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
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
