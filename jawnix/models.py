from __future__ import annotations

import enum
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, event, text
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
    permanent_history_key: Mapped[str] = mapped_column(
        String(64),
        default=lambda: str(uuid.uuid4()),
        index=True,
        nullable=False,
    )
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
    billing_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    lead_rate_cents_per_thousand: Mapped[int | None] = mapped_column(Integer)
    cooldown_window_days: Mapped[int] = mapped_column(
        Integer, default=7, nullable=False
    )
    permanent_history_key: Mapped[str] = mapped_column(
        String(64),
        default=lambda: str(uuid.uuid4()),
        index=True,
        nullable=False,
    )
    last_fulfilled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        index=True,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    agency: Mapped[Agency | None] = relationship()

    __table_args__ = (
        CheckConstraint(
            "NOT billing_enabled OR lead_rate_cents_per_thousand IS NOT NULL",
            name="ck_agent_billing_rate_required",
        ),
        CheckConstraint(
            (
                "lead_rate_cents_per_thousand IS NULL OR "
                "lead_rate_cents_per_thousand BETWEEN 100 AND 2000"
            ),
            name="ck_agent_lead_rate_range",
        ),
        CheckConstraint(
            "cooldown_window_days >= 1",
            name="ck_agent_cooldown_window_days",
        ),
    )


# Canonical domain type. The legacy class/table name remains only as an
# expand/contract persistence compatibility detail.
Customer = Agent


class AgencyMembershipHistory(Base):
    """One period of current membership, retained after every reassignment.

    The permanent history key on both parties is the allocation-time closure:
    keys merge and never split. These rows separately retain the human-readable
    sequence of assignments without making mutable current membership stand in
    for history.
    """

    __tablename__ = "agency_membership_history"
    id: Mapped[int] = mapped_column(
        ID_TYPE,
        primary_key=True,
        autoincrement=True,
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("agents.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    agency_id: Mapped[int] = mapped_column(
        ForeignKey("agencies.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        index=True,
    )
    assigned_by: Mapped[str] = mapped_column(String(160), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        Index(
            "uq_current_agency_membership_per_customer",
            "customer_id",
            unique=True,
            postgresql_where=text("ended_at IS NULL"),
            sqlite_where=text("ended_at IS NULL"),
        ),
    )


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
        CheckConstraint(
            (
                "NOT is_billed OR "
                "(lead_rate_cents_per_thousand IS NOT NULL AND "
                "billing_amount_cents IS NOT NULL AND "
                "billing_amount_cents >= 0)"
            ),
            name="ck_lead_request_frozen_billing",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customer_profiles.user_id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="RESTRICT"), index=True)
    lead_count: Mapped[int] = mapped_column(Integer, nullable=False)
    rows_per_file: Mapped[int] = mapped_column(
        Integer,
        default=lambda context: context.get_current_parameters()["lead_count"],
        nullable=False,
    )
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
    # Billing terms are copied from the Customer at submission. Later toggle
    # or Lead Rate changes therefore cannot reprice in-flight work.
    is_billed: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    lead_rate_cents_per_thousand: Mapped[int | None] = mapped_column(Integer)
    billing_amount_cents: Mapped[int | None] = mapped_column(Integer)
    profile: Mapped[CustomerProfile] = relationship()
    agent: Mapped[Agent] = relationship()
    artifact: Mapped[BatchArtifact | None] = relationship(back_populates="request", uselist=False)
    billing_hold: Mapped[BatchHold | None] = relationship(
        back_populates="request",
        uselist=False,
    )

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


class CreditLedgerEntry(Base):
    """One immutable change to a Customer's Credit Wallet."""

    __tablename__ = "credit_ledger_entries"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("agents.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    actor_user_id: Mapped[str] = mapped_column(String(160), nullable=False)
    batch_request_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("lead_requests.id", ondelete="RESTRICT"),
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        index=True,
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "kind IN ('purchase', 'batch_charge', 'admin_adjustment')",
            name="ck_credit_ledger_kind",
        ),
        CheckConstraint(
            "kind != 'purchase' OR amount_cents > 0",
            name="ck_credit_purchase_positive",
        ),
        CheckConstraint(
            "kind != 'batch_charge' OR amount_cents <= 0",
            name="ck_credit_batch_charge_nonpositive",
        ),
        CheckConstraint(
            "kind != 'batch_charge' OR batch_request_id IS NOT NULL",
            name="ck_credit_batch_charge_request",
        ),
        CheckConstraint(
            (
                "kind != 'admin_adjustment' OR "
                "(amount_cents != 0 AND length(trim(reason)) > 0)"
            ),
            name="ck_credit_adjustment_reason",
        ),
        Index(
            "ix_credit_ledger_customer_created",
            "customer_id",
            "created_at",
        ),
    )


@event.listens_for(CreditLedgerEntry, "before_update", propagate=True)
@event.listens_for(CreditLedgerEntry, "before_delete", propagate=True)
def _credit_ledger_is_append_only(*_args) -> None:
    raise ValueError("Credit Ledger entries are append-only.")


class BatchHold(Base):
    """A billed Batch Request's reservation before distribution commits."""

    __tablename__ = "batch_holds"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("lead_requests.id", ondelete="RESTRICT"),
        unique=True,
        nullable=False,
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("agents.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        default="active",
        index=True,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    request: Mapped[LeadRequest] = relationship(back_populates="billing_hold")

    __table_args__ = (
        CheckConstraint(
            "amount_cents >= 0",
            name="ck_batch_hold_amount_nonnegative",
        ),
        CheckConstraint(
            "status IN ('active', 'captured', 'released')",
            name="ck_batch_hold_status",
        ),
        Index(
            "ix_batch_hold_customer_status",
            "customer_id",
            "status",
        ),
    )


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


class ExclusionList(Base):
    """One typed Customer or administrator upload and its decision state."""

    __tablename__ = "exclusion_lists"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("agents.id", ondelete="RESTRICT"),
        index=True,
    )
    uploaded_by: Mapped[str] = mapped_column(String(160), nullable=False)
    exclusion_type: Mapped[str] = mapped_column(String(24), index=True, nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default="queued", index=True, nullable=False
    )
    global_effective: Mapped[bool] = mapped_column(
        Boolean, default=False, index=True, nullable=False
    )
    total_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    accepted_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    invalid_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duplicate_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pool_impact: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    decision_actor: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    decision_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    nightly_review_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("nightly_reviews.id", ondelete="RESTRICT"),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True, nullable=False
    )
    ingested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "exclusion_type IN ('landline', 'dnc', 'tcpa_litigator')",
            name="ck_exclusion_lists_type",
        ),
    )


class ExclusionPhone(Base):
    """A normalized phone protected by one Exclusion List."""

    __tablename__ = "exclusion_phones"
    exclusion_list_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exclusion_lists.id", ondelete="CASCADE"),
        primary_key=True,
    )
    phone: Mapped[str] = mapped_column(String(10), primary_key=True, index=True)


class NichePolicyRow(Base):
    """One normalized Niche membership in a Customer's state policy."""

    __tablename__ = "niche_policy_rows"
    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # Null is the all-states default.  A state policy replaces, rather than
    # augments, the default policy.
    state: Mapped[str | None] = mapped_column(String(2), index=True)
    mode: Mapped[str] = mapped_column(String(8), nullable=False)
    niche: Mapped[str] = mapped_column(String(160), nullable=False)

    __table_args__ = (
        CheckConstraint("mode IN ('exclude', 'only')", name="ck_niche_policy_rows_mode"),
        UniqueConstraint(
            "customer_id", "state", "mode", "niche",
            name="uq_niche_policy_row",
        ),
    )


class NicheAssignment(Base):
    """Administrator-provided Niche for inventory without a mapped source."""

    __tablename__ = "niche_assignments"
    phone: Mapped[str] = mapped_column(String(10), primary_key=True)
    niche: Mapped[str] = mapped_column(String(160), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class NicheAssignmentUpload(Base):
    """Durable asynchronous import of manual Niche Assignments."""

    __tablename__ = "niche_assignment_uploads"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    uploaded_by: Mapped[str] = mapped_column(String(160), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default="queued", index=True, nullable=False
    )
    total_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    accepted_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    invalid_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duplicate_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    skipped_mapped_rows: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True, nullable=False
    )
    ingested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
    """An audited override of a Lead's delivered title or state.

    A correction outranks the evidence underneath it, so the row records what
    that evidence said at the moment the override was made. Without it a
    correction is an assertion: the Current Listing it disagreed with can be
    superseded by a later Scrape Run, leaving nothing to judge the override
    against. ``based_on_*`` keeps the comparison permanently available.
    """

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
    #: Which kind of evidence this override was made against. ``unknown`` is
    #: reserved for corrections recorded before evidence was captured.
    based_on_kind: Mapped[str] = mapped_column(
        String(24),
        default="unknown",
        nullable=False,
    )
    based_on_observation_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "listing_observations.id",
            ondelete="SET NULL",
            name="fk_lead_correction_based_on_observation",
        ),
        index=True,
    )
    based_on_title: Mapped[str] = mapped_column(
        Text,
        default="",
        nullable=False,
    )
    based_on_state: Mapped[str] = mapped_column(
        String(2),
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
        CheckConstraint(
            (
                "based_on_kind IN ('current_listing', 'legacy_snapshot', "
                "'prior_correction', 'none', 'unknown')"
            ),
            name="ck_lead_correction_based_on_kind",
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
        # Covering indexes so shared-history Lead counts stay index-only.
        Index("ix_distribution_events_agency_lead", "agency_id", "lead_id"),
        Index("ix_distribution_events_agent_lead", "agent_id", "lead_id"),
        # Serves the per-Customer first/last delivered bounds as index probes.
        Index(
            "ix_distribution_events_agent_delivered",
            "agent_id",
            "delivered_at",
        ),
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
    parts: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
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


class ScraperRuntimeConfigurationRevision(Base):
    """Append-only evidence for a successful Scale runtime configuration save.

    This is intentionally not a ScraperConfiguration relationship. Scale's
    mutable worker/queue controls and Jawnix's immutable acquisition versions
    are separate authorities.
    """

    __tablename__ = "scraper_runtime_configuration_revisions"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    before_checksum: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    after_checksum: Mapped[str] = mapped_column(
        String(64),
        index=True,
        nullable=False,
    )
    configuration: Mapped[dict] = mapped_column(JSON, nullable=False)
    effects: Mapped[dict] = mapped_column(JSON, nullable=False)
    enqueue_requested: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        index=True,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        index=True,
        nullable=False,
    )


class KeywordHistory(Base):
    """One normalized keyword observed through one provenance path."""

    __tablename__ = "keyword_history"
    id: Mapped[int] = mapped_column(
        ID_TYPE,
        primary_key=True,
        autoincrement=True,
    )
    term: Mapped[str] = mapped_column(String(320), index=True, nullable=False)
    origin: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        index=True,
        nullable=False,
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        index=True,
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "term",
            "origin",
            name="uq_keyword_history_term_origin",
        ),
        CheckConstraint(
            "origin IN ('legacy_enqueue_log', 'legacy_keyword_history', "
            "'legacy_businesses', 'active_list', 'winner', "
            "'accepted_save')",
            name="ck_keyword_history_origin",
        ),
        CheckConstraint(
            "first_seen_at <= last_seen_at",
            name="ck_keyword_history_seen_range",
        ),
    )


class KeywordHistoryImport(Base):
    """Durable proof that one exact legacy snapshot was imported."""

    __tablename__ = "keyword_history_imports"
    id: Mapped[int] = mapped_column(
        ID_TYPE,
        primary_key=True,
        autoincrement=True,
    )
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    checksum: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
    )
    report: Mapped[dict] = mapped_column(JSON, nullable=False)
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )


class KeywordGenerationDraftRecord(Base):
    """A Jawnix-owned, review-only keyword generation draft."""

    __tablename__ = "keyword_generation_drafts"
    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    administrator_id: Mapped[uuid.UUID] = mapped_column(
        index=True,
        nullable=False,
    )
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    seed_keyword: Mapped[str | None] = mapped_column(String(200))
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    terms: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    exclusion_metrics: Mapped[dict] = mapped_column(JSON, nullable=False)
    candidate_metrics: Mapped[dict] = mapped_column(JSON, nullable=False)
    excluded_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    acceptance_status: Mapped[str] = mapped_column(
        String(16),
        default="pending",
        index=True,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        index=True,
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        index=True,
        nullable=False,
    )
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    __table_args__ = (
        CheckConstraint(
            "mode IN ('broad', 'adjacent')",
            name="ck_keyword_generation_drafts_mode",
        ),
        CheckConstraint(
            "acceptance_status IN ('pending', 'accepted')",
            name="ck_keyword_generation_drafts_acceptance_status",
        ),
        CheckConstraint(
            "excluded_count >= 0",
            name="ck_keyword_generation_drafts_excluded_count",
        ),
        CheckConstraint(
            "created_at < expires_at",
            name="ck_keyword_generation_drafts_expiry",
        ),
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


class SharedHistoryLeadCount(Base):
    """Persisted distributed-Lead count for one permanent-history subject set.

    Recomputing the count walks millions of distribution_events rows, so the
    Agency read models serve this row (refreshing it in the background once
    stale) instead of counting per request. The key encodes the subject
    Customer and Agency ids, so a history merge produces a new key and never
    reads a stale entry.
    """

    __tablename__ = "shared_history_lead_counts"
    subject_key: Mapped[str] = mapped_column(Text, primary_key=True)
    lead_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
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


class UserAccountMigrationRun(Base):
    """Durable resume point for the one-time external identity migration."""

    __tablename__ = "user_account_migration_runs"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    input_checksum: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
    )
    plan_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24),
        default="in_progress",
        nullable=False,
    )
    operator: Mapped[str] = mapped_column(String(160), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    backup_receipt_checksum: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    backup_snapshot: Mapped[str] = mapped_column(String(160), nullable=False)
    backup_receipts: Mapped[list[dict]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('in_progress', 'completed')",
            name="ck_user_account_migration_runs_status",
        ),
    )


class UserAccountMigrationMapping(Base):
    """Mutable interruption journal; the final artifact is separate."""

    __tablename__ = "user_account_migration_mappings"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_account_migration_runs.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("agents.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    customer_slug: Mapped[str] = mapped_column(String(80), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    agency_id: Mapped[int | None] = mapped_column(
        ForeignKey("agencies.id", ondelete="RESTRICT"),
        index=True,
    )
    agency_slug: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    prior_auth_user_id: Mapped[uuid.UUID | None] = mapped_column()
    invited_auth_user_id: Mapped[uuid.UUID | None] = mapped_column()
    invitation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_account_invitations.id", ondelete="RESTRICT")
    )
    state: Mapped[str] = mapped_column(
        String(32),
        default="planned",
        index=True,
        nullable=False,
    )
    deactivation_state: Mapped[str] = mapped_column(
        String(40),
        default="not_started",
        nullable=False,
    )
    agency_before_id: Mapped[int | None] = mapped_column()
    agency_result: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    history_counts: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "row_number",
            name="uq_user_account_migration_run_row",
        ),
        CheckConstraint(
            (
                "state IN ('planned', 'dispatching', 'failed', "
                "'invited_pending', 'active')"
            ),
            name="ck_user_account_migration_mappings_state",
        ),
    )


class UserAccountMigrationArtifact(Base):
    """Append-only reconciliation proof for one completed migration run."""

    __tablename__ = "user_account_migration_artifacts"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_account_migration_runs.id", ondelete="RESTRICT"),
        unique=True,
        nullable=False,
    )
    checksum: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    contents: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )


class QuarantinedRow(Base):
    __tablename__ = "quarantined_rows"
    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    row_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
