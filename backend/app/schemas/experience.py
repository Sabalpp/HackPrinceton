"""Shared, additive contracts for authenticated three-role experiences."""

from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.contracts import (
    AssistanceCategory,
    CareRequest,
    CareRequestStatus,
    CheckIn,
    HealthSummary,
    ParentProfile,
    PersonSummary,
    SafetyIncident,
)

MetricName = Literal["steps", "resting_heart_rate", "sleep_minutes", "systolic", "diastolic"]
Symptom = Literal[
    "chest_pain",
    "shortness_of_breath",
    "new_weakness",
    "new_numbness",
    "vision_change",
    "difficulty_speaking",
    "sudden_severe_headache",
]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


class ActionIn(Input):
    action_id: str = Field(min_length=16, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")


class SessionInfo(BaseModel):
    authenticated: bool
    person: PersonSummary | None = None
    parent_ids: list[str] = Field(default_factory=list)
    csrf_token: str | None = None
    expires_at: datetime | None = None
    demo_available: bool = False
    demo_session: bool = False
    registration_available: bool = False


class LoginIn(Input):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def email_format(cls, value: str) -> str:
        # Account identifier only: ownership is not claimed to be email-verified.
        value = value.strip().casefold()
        if value.count("@") != 1 or any(c.isspace() for c in value):
            raise ValueError("Enter an email address")
        local, domain = value.split("@")
        if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
            raise ValueError("Enter an email address")
        return value


class RegisterIn(LoginIn):
    password: str = Field(min_length=12, max_length=128)
    display_name: str = Field(min_length=1, max_length=80)
    invitation_token: str | None = Field(default=None, min_length=32, max_length=128)
    share_health: bool = False
    share_checkins: bool = False


class DemoLoginIn(Input):
    role: Literal["child", "parent", "responder"]


class InvitationLookup(Input):
    token: str = Field(min_length=32, max_length=128)


class InvitationInfo(BaseModel):
    role: Literal["parent", "responder"]
    display_name: str
    invited_by: str
    expires_at: datetime


class InviteIssued(BaseModel):
    parent_id: str
    invitation_token: str
    expires_at: datetime
    role: Literal["parent", "responder"]
    display_name: str


class CreateParentIn(ActionIn):
    display_name: str = Field(min_length=1, max_length=80)
    timezone: str = Field(max_length=64)
    country_code: Literal["US", "NP", "OTHER"] = "OTHER"

    @field_validator("timezone")
    @classmethod
    def known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError:
            raise ValueError("Choose an IANA timezone") from None
        return value


class CreateInviteIn(ActionIn):
    parent_id: str = Field(min_length=1, max_length=80)
    role: Literal["parent", "responder"]
    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    relationship: str = Field(default="neighbor", min_length=1, max_length=40)
    categories: list[AssistanceCategory] = Field(default_factory=list, max_length=8)
    emergency_contact: bool = False


class AgentStatus(BaseModel):
    status: Literal["healthy", "starting", "stale", "offline"]
    last_heartbeat_at: datetime | None = None
    messaging: Literal["console", "photon"]
    messaging_ready: bool
    detail: str
    messaging_platform: Literal["sms", "imessage"] | None = None
    messaging_opted_out: bool = False


class SharingMember(BaseModel):
    member_id: str
    display_name: str
    relationship: str
    share_health: bool
    share_checkins: bool


class SharingIn(Input):
    member_id: str = Field(min_length=1, max_length=80)
    share_health: bool
    share_checkins: bool


class ResponderMember(BaseModel):
    responder_id: str
    display_name: str
    relationship: str
    active: bool
    emergency_contact: bool


class ResponderApprovalIn(Input):
    active: bool
    emergency_contact: bool


class ParentHome(BaseModel):
    parent: ParentProfile
    requests: list[CareRequest]
    active_incident: SafetyIncident | None = None
    latest_checkin: CheckIn | None = None
    health: HealthSummary | None = None
    sharing: list[SharingMember]
    responders: list[ResponderMember]
    agent: AgentStatus


class CheckInIn(ActionIn):
    response: Literal["okay", "help"]
    note: str = Field(default="", max_length=500)


class HelpIn(ActionIn):
    request_type: AssistanceCategory
    details: str = Field(min_length=1, max_length=1000)
    requested_time: str | None = Field(default=None, max_length=120)


class RequestDecisionIn(ActionIn):
    decision: Literal["approve", "cancel"]


class ResponderTask(BaseModel):
    request_id: str
    parent_first_name: str
    request_type: AssistanceCategory
    details: str
    requested_time: str | None = None
    status: CareRequestStatus
    estimated_arrival_minutes: int | None = None
    updated_at: datetime


class ResponderTasks(BaseModel):
    tasks: list[ResponderTask]
    agent: AgentStatus


class ResponderReplyIn(ActionIn):
    decision: Literal["accept", "decline", "complete"]
    eta_minutes: int | None = Field(default=None, ge=1, le=1440)
    note: str = Field(default="", max_length=500)


class BPReadingIn(ActionIn):
    systolic: int = Field(ge=50, le=300)
    diastolic: int = Field(ge=20, le=200)
    measured_at: datetime
    device_validated: bool | None = None
    symptoms_checked: bool = False
    adult_nonpregnant: bool = False
    symptoms: list[Symptom] = Field(default_factory=list, max_length=7)

    @model_validator(mode="after")
    def valid_reading(self):
        if self.systolic <= self.diastolic:
            raise ValueError("Check the systolic and diastolic numbers")
        if self.measured_at.tzinfo is None:
            raise ValueError("measured_at requires a timezone")
        if self.symptoms and not self.symptoms_checked:
            raise ValueError("Confirm the symptom checklist")
        return self


class ReferenceIn(Input):
    metric: MetricName
    value: float = Field(ge=0, le=200000)
    note: str = Field(default="", max_length=500)


class ReferenceInfo(BaseModel):
    metric: MetricName
    value: float
    note: str
    declared_at: datetime
    provisional: bool = True


class BPAssessment(BaseModel):
    level: str
    title: str
    message: str
    action: str
    source: str = "manual_cuff"
    override_allowed: bool = False


class BPReadingInfo(BaseModel):
    reading_id: str
    systolic: int
    diastolic: int
    measured_at: datetime
    source: str
    data_mode: str
    device_validated: bool | None
    symptoms_checked: bool
    symptoms: list[Symptom]
    assessment: BPAssessment


class PersonalComparison(BaseModel):
    status: str
    basis: str | None = None
    delta: float | None = None
    band_low: float | None = None
    band_high: float | None = None
    message: str


class PersonalMetric(BaseModel):
    metric: MetricName
    status: str
    eligible_days: int
    required_days: int
    window_days: int
    min_readings_per_day: int
    eligible_readings: int
    median: float | None = None
    mad: float | None = None
    declared_reference: float | None = None
    explanation: str
    excluded_counts: dict[str, int] = Field(default_factory=dict)
    comparison: PersonalComparison | None = None


class EvidenceSource(BaseModel):
    title: str
    url: str


class PersonalHealthReport(BaseModel):
    parent_id: str
    metrics: list[PersonalMetric]
    references: list[ReferenceInfo]
    blood_pressure: list[BPReadingInfo]
    agent: AgentStatus
    generated_at: datetime
    evidence: list[EvidenceSource]
    explanation: str


class SensorMetric(BaseModel):
    """A source-reported value, with its original time basis and freshness."""

    metric: str
    label: str
    unit: str
    value: float | None = None
    source: Literal["ble", "google_health"] | None = None
    kind: Literal["sample", "interval", "daily", "session"] | None = None
    observed_at: datetime | None = None
    ended_at: datetime | None = None
    local_date: str | None = None
    freshness: Literal["fresh", "stale", "missing", "invalid"] = "missing"
    age_seconds: float | None = None
    message: str


class SensorPoint(BaseModel):
    observed_at: datetime
    value: float


class SensorHealthReport(BaseModel):
    parent_id: str
    connection_status: Literal["not_linked", "available", "unavailable"]
    generated_at: datetime
    live_heart_rate: SensorMetric
    cloud_metrics: list[SensorMetric]
    recent_heart_rate: list[SensorPoint] = Field(default_factory=list)
    message: str
    last_daily_sync_at: datetime | None = None
    daily_sync_status: Literal["pending", "ok", "unavailable"] = "pending"
    data_mode: Literal["live"] = "live"


# --------------------------------------------------------------------------- conversation


class ChatMessage(BaseModel):
    """One message in a person's conversation with FarCare (any channel)."""

    id: str
    direction: Literal["in", "out"]
    text: str
    channel: str = Field(description="imessage | sms | voice | app | in_app | console | ...")
    status: str
    at: datetime


class ChatThread(BaseModel):
    messages: list[ChatMessage]
    connected_by_text: bool = Field(
        description="True once this person has texted their FarCare number (iMessage/SMS)"
    )
    messaging: Literal["console", "photon"]


class ChatIn(Input):
    text: str = Field(min_length=1, max_length=1000)
