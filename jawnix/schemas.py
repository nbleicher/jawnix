from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)

from .states import normalize_states


class SessionExchange(BaseModel):
    access_token: str = Field(min_length=20)
    requested_next: str | None = Field(default=None, max_length=200)


class AdminMFAAccessToken(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_token: str = Field(min_length=20, max_length=10000)


class AdminMFAEnrollStart(AdminMFAAccessToken):
    slot: str = Field(pattern="^(primary|backup)$")


class AdminMFACode(AdminMFAAccessToken):
    code: str = Field(min_length=6, max_length=32)

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        normalized = "".join(character for character in value if character.isdigit())
        if len(normalized) != 6:
            raise ValueError("Enter the six-digit authenticator code.")
        return normalized


class AdminMFAChallenge(AdminMFACode):
    factor_id: uuid.UUID


class AdminMFAReplacementStart(AdminMFAAccessToken):
    lost_factor_id: uuid.UUID


class ProfileUpdate(BaseModel):
    first_name: str = Field(default="", max_length=120)
    last_name: str = Field(default="", max_length=120)
    phone: str = Field(default="", max_length=40)
    licensed_states: list[str]

    @field_validator("licensed_states")
    @classmethod
    def valid_states(cls, value: list[str]) -> list[str]:
        return normalize_states(value)


class RequestCreate(BaseModel):
    lead_count: int = Field(ge=1, le=100_000)
    state_mode: str = Field(default="all_saved", pattern="^(all_saved|selected)$")
    states: list[str] = Field(default_factory=list)

    @field_validator("states")
    @classmethod
    def valid_states(cls, value: list[str]) -> list[str]:
        return normalize_states(value)

    @model_validator(mode="after")
    def selected_requires_states(self):
        if self.state_mode == "selected" and not self.states:
            raise ValueError("Select at least one state for this request.")
        return self


class CustomerMappingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: int = Field(
        validation_alias=AliasChoices("customer_id", "agent_id")
    )
    confirmed: bool = True


class UserAccountReplace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auth_user_id: uuid.UUID
    email: EmailStr
    reason: str = Field(
        default="User Account replacement",
        min_length=1,
        max_length=2000,
    )


class NightlyDeliveryReconcile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: str = Field(pattern="^(delivered|not_delivered)$")
    message_id: str | None = Field(default=None, max_length=120)
    reason: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def delivered_requires_message_id(self):
        if self.outcome == "delivered" and not self.message_id:
            raise ValueError(
                "A delivered Nightly Review requires its Telegram "
                "message ID."
            )
        if self.outcome == "not_delivered" and self.message_id is not None:
            raise ValueError(
                "A Nightly Review confirmed not delivered cannot have "
                "a Telegram message ID."
            )
        return self


class AgencyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    active: bool
    reason: str = Field(
        default="Agency record updated",
        min_length=1,
        max_length=2000,
    )


class AgencyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    slug: str | None = Field(default=None, min_length=1, max_length=80)
    reason: str = Field(min_length=1, max_length=2000)


class AgencyAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agency_id: int | None = None
    reason: str = Field(min_length=1, max_length=2000)
    confirmed: bool


class CustomerUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    agency_id: int | None = None
    active: bool
    reason: str = Field(
        default="Customer record updated",
        min_length=1,
        max_length=2000,
    )


class DeleteConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm_slug: str = Field(min_length=1, max_length=80)


class CustomerDelete(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm_slug: str = Field(min_length=1, max_length=80)
    hard_delete: bool = False
    reason: str = Field(
        default="Administrative lifecycle action",
        min_length=1,
        max_length=2000,
    )


class CustomerCreate(BaseModel):
    """Create a durable Customer and invite its first User Account.

    There is deliberately no password field. Administrators provision access
    by invitation only, so a posted credential is a validation failure rather
    than something Jawnix could accept and store.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    email: EmailStr
    slug: str | None = Field(default=None, min_length=1, max_length=80)
    agency_id: int | None = None
    first_name: str = Field(default="", max_length=120)
    last_name: str = Field(default="", max_length=120)


class UserAccountInvite(BaseModel):
    """Invite a replacement User Account for an existing Customer."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    first_name: str = Field(default="", max_length=120)
    last_name: str = Field(default="", max_length=120)
    reason: str = Field(
        default="Invited a replacement User Account",
        min_length=1,
        max_length=2000,
    )


CustomerAdminTone = Literal["neutral", "info", "success", "warning", "danger"]


class CustomerAdminStatus(BaseModel):
    """One standing, stated in administrator vocabulary.

    Screens render the label and tone directly, so the internal lifecycle
    values never have to be re-translated per surface.
    """

    label: str
    description: str
    tone: CustomerAdminTone


class CustomerDirectoryAgency(BaseModel):
    id: int
    name: str
    active: bool


class CustomerDirectoryRow(BaseModel):
    id: int
    slug: str
    name: str
    agency_id: int | None
    agency: str
    licensed_states: list[str]
    #: The durable party's standing, which access changes never affect.
    customer_status: CustomerAdminStatus
    #: The replaceable authentication's standing.
    account_status: CustomerAdminStatus
    account_email: str
    last_activity_at: datetime | None
    problems: list[str]
    href: str


class CustomerDirectoryFilters(BaseModel):
    query: str
    status: Literal["all", "active", "deactivated"]
    agency_id: int | None
    state: str
    problems_only: bool


class CustomerDirectoryOut(BaseModel):
    """The searchable Customer directory that replaces hierarchy editing."""

    filters: CustomerDirectoryFilters
    agencies: list[CustomerDirectoryAgency]
    states: list[str]
    total: int
    matched: int
    customers: list[CustomerDirectoryRow]


class CustomerRecord(BaseModel):
    """Durable Customer identity. Replacing access never changes any of it."""

    id: int
    slug: str
    name: str
    agency_id: int | None
    agency: str
    active: bool
    licensed_states: list[str]
    status: CustomerAdminStatus
    last_activity_at: datetime | None


class CustomerHistory(BaseModel):
    """Permanent history a User Account replacement must never reset."""

    requests: int
    distributions: int
    outcomes: int
    reports: int
    first_delivered_at: datetime | None
    last_delivered_at: datetime | None


class UserAccountRecord(BaseModel):
    """Replaceable authentication. Never the party that owns history."""

    auth_user_id: str
    email: str
    name: str
    active: bool
    created_at: datetime
    replaced_at: datetime | None
    replaced_by_auth_user_id: str | None


class UserAccountInvitationRecord(BaseModel):
    """An offer of access that has not been accepted yet."""

    id: str
    email: str
    invited_at: datetime
    replaces_auth_user_id: str | None
    status: CustomerAdminStatus


class CustomerActivityEntry(BaseModel):
    id: str
    action: str
    label: str
    actor: str
    reason: str
    created_at: datetime


class CustomerDeletionGuard(BaseModel):
    """What the existing dependency and tombstone rules currently allow."""

    dependencies: dict[str, int]
    requires_deactivation: bool
    can_hard_delete: bool
    tombstoned: bool


class CustomerDetailsOut(BaseModel):
    """Customer details, with durable identity separated from access."""

    customer: CustomerRecord
    history: CustomerHistory
    user_account: UserAccountRecord | None
    invitation: UserAccountInvitationRecord | None
    former_accounts: list[UserAccountRecord]
    activity: list[CustomerActivityEntry]
    deletion: CustomerDeletionGuard


class ProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    user_id: uuid.UUID
    email: str
    first_name: str
    last_name: str
    phone: str
    licensed_states: list[str]
    customer_id: int | None
    mapping_confirmed_at: datetime | None


class RequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    lead_count: int
    state_mode: str
    states_snapshot: list[str]
    status: str
    available_count: int | None
    status_message: str
    created_at: datetime
    delivered_at: datetime | None


class CustomerOverviewStatus(BaseModel):
    """Presentation-safe Batch Request state.

    The backend status and status_message deliberately are not part of this
    contract. Customer screens should not need to understand the fulfillment
    state machine.
    """

    label: str
    description: str
    tone: Literal["neutral", "info", "success", "warning", "danger"]


class CustomerOverviewRequest(BaseModel):
    id: uuid.UUID
    lead_count: int
    states: list[str]
    submitted_at: datetime
    delivered_at: datetime | None
    status: CustomerOverviewStatus


class CustomerOverviewDelivery(BaseModel):
    request_id: uuid.UUID
    lead_count: int
    states: list[str]
    delivered_at: datetime


class CustomerOverviewAction(BaseModel):
    kind: Literal[
        "request_batch",
        "submit_feedback",
        "review_request",
        "review_account",
        "add_licensed_states",
    ]
    label: str
    description: str
    href: str


class CustomerOverviewOut(BaseModel):
    """Stable aggregate read contract for the Customer application."""

    first_name: str
    licensed_states: list[str]
    current_request: CustomerOverviewRequest | None
    recent_deliveries: list[CustomerOverviewDelivery]
    next_action: CustomerOverviewAction
    primary_actions: list[CustomerOverviewAction]


CustomerMilestoneKey = Literal[
    "submitted",
    "under_review",
    "preparing_batch",
    "delivered",
]


class CustomerMilestone(BaseModel):
    """One node of the Customer-facing Batch Request journey.

    `state` carries the whole meaning of the node, so a client can render the
    graph without animation and describe it in text. `not_reached` is
    deliberately distinct from `upcoming`: a stopped request never arrives.
    """

    key: CustomerMilestoneKey
    label: str
    description: str
    state: Literal[
        "complete",
        "current",
        "paused",
        "stopped",
        "upcoming",
        "not_reached",
    ]
    occurred_at: datetime | None


class CustomerRequestPause(BaseModel):
    """An explained wait inside the journey rather than an ending.

    Waiting for Inventory is the only pause the Customer vocabulary exposes.
    It has no next action because there is nothing the Customer can do.
    """

    kind: Literal["inventory_wait"]
    milestone_key: CustomerMilestoneKey
    label: str
    description: str


class CustomerRequestOutcome(BaseModel):
    """Why a Batch Request stopped short of Delivered."""

    kind: Literal["rejected", "canceled", "failed"]
    milestone_key: CustomerMilestoneKey
    label: str
    description: str
    tone: Literal["neutral", "info", "success", "warning", "danger"]
    occurred_at: datetime | None


class CustomerRequestMilestones(BaseModel):
    milestones: list[CustomerMilestone]
    current_key: CustomerMilestoneKey | None
    pause: CustomerRequestPause | None
    outcome: CustomerRequestOutcome | None


class CustomerRequestAction(BaseModel):
    kind: Literal[
        "request_batch",
        "submit_feedback",
        "contact_support",
    ]
    label: str
    description: str
    href: str


class CustomerBatchArtifact(BaseModel):
    """The safe Customer projection of one delivered Batch Artifact."""

    filename: str
    row_count: int
    expires_at: datetime | None
    available: bool
    download_href: str | None


class CustomerRequestDetail(BaseModel):
    """One Batch Request as the Customer application reads it."""

    id: uuid.UUID
    lead_count: int
    states: list[str]
    submitted_at: datetime
    delivered_at: datetime | None
    status: CustomerOverviewStatus
    milestones: CustomerRequestMilestones
    can_cancel: bool
    next_action: CustomerRequestAction | None
    receipt_href: str
    artifact: CustomerBatchArtifact | None


class CustomerRequestLimits(BaseModel):
    """The domain bounds the guided stages validate against."""

    minimum_lead_count: int
    maximum_lead_count: int
    licensed_states: list[str]


class CustomerRequestBlocker(BaseModel):
    """Why the guided flow cannot start at all."""

    reason: Literal["mapping_unconfirmed", "no_licensed_states"]
    label: str
    description: str
    action: CustomerOverviewAction


class CustomerRequestWorkspaceOut(BaseModel):
    """Stable read contract for the guided Batch Request screen."""

    limits: CustomerRequestLimits
    blocker: CustomerRequestBlocker | None
    requests: list[CustomerRequestDetail]


class CustomerRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The client mints this once per guided flow. Replaying it returns the
    # request that already exists instead of creating a second one.
    idempotency_key: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9._:-]+$")
    lead_count: int = Field(ge=1, le=100_000)
    state_mode: Literal["all_saved", "selected"] = "all_saved"
    states: list[str] = Field(default_factory=list)

    @field_validator("states")
    @classmethod
    def valid_states(cls, value: list[str]) -> list[str]:
        return normalize_states(value)

    @model_validator(mode="after")
    def selected_requires_states(self):
        if self.state_mode == "selected" and not self.states:
            raise ValueError("Select at least one Licensed State for this request.")
        return self


class CustomerRequestReceipt(BaseModel):
    """The submit response.

    `created` is False when an idempotent replay resolved to the request the
    first attempt already made, which is what lets a client retry safely.
    """

    created: bool
    request: CustomerRequestDetail


class FeedbackLookup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phone: str


class FeedbackSearch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=2, max_length=100)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        query = value.strip()
        if len(query) < 2:
            raise ValueError("Search requires at least two characters.")
        return query


class FeedbackCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    distribution_event_id: int = Field(gt=0)
    disposition: str = Field(
        pattern=(
            "^(no_contact|not_interested|positive_response|"
            "appointment_booked|appointment_canceled|"
            "appointment_no_show|invalid_phone|wrong_business|"
            "do_not_contact|other)$"
        )
    )
    note: str = Field(default="", max_length=2000)
    quality_rating: str | None = Field(
        default=None,
        pattern="^(good|poor)$",
    )
    quality_note: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def other_requires_note(self):
        if self.disposition == "other" and not self.note.strip():
            raise ValueError("Other disposition requires a note.")
        return self


class OutcomeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(
        pattern=(
            "^(good|poor|positive_response|appointment_booked|"
            "appointment_canceled|appointment_no_show)$"
        )
    )
    appointment_at: datetime | None = None
    note: str = Field(default="", max_length=2000)
    supersedes_outcome_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def validate_appointment(self):
        if self.kind == "appointment_booked" and self.appointment_at is None:
            raise ValueError("Appointment Booked requires a scheduled date and time.")
        if self.kind != "appointment_booked" and self.appointment_at is not None:
            raise ValueError("Only Appointment Booked accepts a scheduled date and time.")
        return self


class OutcomeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    distribution_event_id: int
    kind: str
    appointment_at: datetime | None
    note: str
    supersedes_outcome_id: uuid.UUID | None
    created_at: datetime


class SourceSegmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=160)
    niche: str = Field(min_length=1, max_length=160)
    query: str = Field(min_length=1, max_length=320)
    geography: str = Field(min_length=1, max_length=320)
    parameters: dict = Field(default_factory=dict)


class AnomalyThresholdsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    down_fraction: float = Field(default=0.5, gt=0, lt=1)
    up_multiplier: float = Field(default=2.0, gt=1)
    history_runs: int = Field(default=7, ge=1, le=30)


class ScraperConfigurationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=2000)
    segments: list[SourceSegmentInput] = Field(min_length=1)
    anomaly_thresholds: AnomalyThresholdsInput = Field(
        default_factory=AnomalyThresholdsInput
    )

    @model_validator(mode="after")
    def unique_segment_keys(self):
        keys = [segment.key.strip().lower() for segment in self.segments]
        if len(keys) != len(set(keys)):
            raise ValueError("Source Segment keys must be unique.")
        return self


class ActionReason(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=2000)


class LeadCorrectionApply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=500)
    state: str | None = None
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("state")
    @classmethod
    def valid_optional_state(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_states([value])[0]

    @model_validator(mode="after")
    def has_correction(self):
        if self.title is None and self.state is None:
            raise ValueError("Correct the title, state, or both.")
        if self.title is not None and not self.title.strip():
            raise ValueError("Corrected title cannot be empty.")
        return self


class LeadReportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(
        pattern=(
            "^(invalid_phone|wrong_business_or_title|wrong_state|duplicate|"
            "do_not_contact_or_legal|other)$"
        )
    )
    details: str = Field(default="", max_length=2000)


class LeadReportNote(BaseModel):
    """The required administrator note behind one Lead Report decision.

    Dismissing and suppressing take nothing else: neither proposes a value,
    so neither has anywhere for a title or state to go.
    """

    model_config = ConfigDict(extra="forbid")

    note: str = Field(min_length=1, max_length=2000)


class LeadReportCorrect(LeadReportNote):
    """Correcting is the one resolution that proposes a replacement value."""

    title: str | None = Field(default=None, max_length=500)
    state: str | None = None

    @field_validator("state")
    @classmethod
    def valid_optional_state(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_states([value])[0]

    @model_validator(mode="after")
    def correction_has_value(self):
        if self.title is None and self.state is None:
            raise ValueError(
                "A Lead Correction must propose a title, a state, or both."
            )
        if self.title is not None and not self.title.strip():
            raise ValueError("Corrected title cannot be empty.")
        return self


class SourceNicheDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    niche: str = Field(min_length=1, max_length=160)
    reason: str = Field(min_length=1, max_length=2000)


class RecommendationDecision(BaseModel):
    """A Source Recommendation decision, bound to the evidence it was made on.

    Telegram binds every decision to the evidence checksum printed on the card
    and refuses a callback whose evidence has moved on. A caller that shows the
    evidence sends back the checksum it showed, so approving numbers that have
    since changed is refused rather than quietly applied to different ones.

    Optional because the legacy administrator page decides without ever
    rendering the evidence; unsent means unbound, exactly as today.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    reason: str = Field(min_length=1, max_length=2000)
    evidence_checksum: str | None = Field(
        default=None,
        max_length=64,
        validation_alias=AliasChoices(
            "evidenceChecksum",
            "evidence_checksum",
        ),
    )
