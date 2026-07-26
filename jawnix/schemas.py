from __future__ import annotations

import uuid
from datetime import datetime

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
    email: EmailStr
    first_name: str = Field(default="", max_length=120)
    last_name: str = Field(default="", max_length=120)
    password: str = Field(min_length=8, max_length=256)


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


class LeadReportResolve(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str = Field(pattern="^(dismissed|corrected|suppressed)$")
    note: str = Field(min_length=1, max_length=2000)
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
        if (
            self.action == "corrected"
            and self.title is None
            and self.state is None
        ):
            raise ValueError(
                "Corrected report resolution requires a title or state."
            )
        return self
