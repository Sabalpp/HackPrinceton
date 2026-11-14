"""FarCare shared contracts.

This module is the SINGLE SOURCE OF TRUTH for every payload that crosses a module
boundary (backend <-> frontend, ingestion sources -> backend). It is exported to
`packages/contracts/` (JSON Schema + OpenAPI) and the frontend's TypeScript types are
generated from that export. Changing anything here is a contract change: follow the
process in AGENTS.md ("Changing a shared contract").
"""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ID_PATTERN = r"^[A-Za-z0-9_\-:.]{1,128}$"


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- enums


class DataMode(StrEnum):
    live = "live"
    simulated = "simulated"


class AssistanceCategory(StrEnum):
    grocery_assistance = "grocery_assistance"
    transportation = "transportation"
    meal_assistance = "meal_assistance"
    medication_pickup = "medication_pickup"
    household_help = "household_help"
    companionship = "companionship"
    urgent_check_in = "urgent_check_in"  # used by emergency escalation only
    other = "other"


class CareRequestStatus(StrEnum):
    created = "created"
    awaiting_approval = "awaiting_approval"
    approved = "approved"
    sent = "sent"
    accepted = "accepted"
    completed = "completed"
    declined = "declined"
    cancelled = "cancelled"
    expired = "expired"
    failed = "failed"


class CareRequestOrigin(StrEnum):
    conversation = "conversation"
    emergency = "emergency"


class IncidentStatus(StrEnum):
    checking = "checking"  # safety check sent to parent, waiting for OK / HELP
    escalated = "escalated"  # help requested or no response: family + responders alerted
    responder_en_route = "responder_en_route"
    resolved = "resolved"


class EscalationReason(StrEnum):
    help_requested = "help_requested"
    no_response = "no_response"
    parent_reported_emergency = "parent_reported_emergency"


class IncidentResolution(StrEnum):
    parent_ok = "parent_ok"
    responder_confirmed = "responder_confirmed"
    family_resolved = "family_resolved"


class PersonRole(StrEnum):
    parent = "parent"
    child = "child"
    responder = "responder"
    admin = "admin"


# --------------------------------------------------------------------------- ingestion


class HealthObservation(Contract):
    """One normalized daily health observation (Fitbit via Google Health API, or simulated)."""

    observation_id: str = Field(pattern=ID_PATTERN, examples=["health_001"])
    parent_id: str = Field(pattern=ID_PATTERN)
    source: Literal["fitbit", "simulated"]
    data_mode: DataMode
    timestamp: datetime = Field(description="Start of the observed day (UTC, ISO-8601)")
    steps: int | None = Field(default=None, ge=0, le=200_000)
    resting_heart_rate: int | None = Field(default=None, ge=20, le=250)
    sleep_minutes: int | None = Field(default=None, ge=0, le=1440)


class FallSuspectedEvent(Contract):
    """A possible-fall event from a detector. NOT a medically validated detection."""

    event_id: str = Field(pattern=ID_PATTERN, examples=["fall_001"])
    parent_id: str = Field(pattern=ID_PATTERN)
    event_type: Literal["fall.suspected"]
    source: Literal["simulated_imu", "replayed_dataset", "device"]
    timestamp: datetime
    confidence: float = Field(
        ge=0,
        le=1,
        description="Detector output score. NOT a validated clinical probability.",
    )
    data_mode: DataMode


class ResponderReply(Contract):
    """A responder's structured answer to a care request (parsed from their message)."""

    request_id: str = Field(pattern=ID_PATTERN)
    responder_id: str = Field(pattern=ID_PATTERN)
    response: Literal["accepted", "declined", "completed", "need_more_help"]
    estimated_arrival_minutes: int | None = Field(default=None, ge=0, le=24 * 60)


class FallIngestResult(Contract):
    event_id: str
    incident_id: str | None
    duplicate: bool
    detail: str


class HealthIngestResult(Contract):
    accepted: int


# --------------------------------------------------------------------------- read models


class PersonSummary(Contract):
    person_id: str
    display_name: str
    role: PersonRole
    reachable: bool = Field(description="True once this person has messaged FarCare (opt-in)")


class ParentProfile(Contract):
    parent_id: str
    display_name: str
    timezone: str
    emergency_number: str | None


class CareCircleMember(Contract):
    responder_id: str
    display_name: str
    relationship: str
    categories: list[AssistanceCategory]
    emergency_contact: bool
    priority: int
    reachable: bool


class CareRequest(Contract):
    request_id: str
    parent_id: str
    request_type: AssistanceCategory
    status: CareRequestStatus
    responder_id: str | None
    responder_name: str | None = None
    parent_approved: bool
    approval_basis: Literal["parent_reply", "emergency_policy"] | None = None
    origin: CareRequestOrigin
    details: str = ""
    requested_time: str | None = None
    estimated_arrival_minutes: int | None = None
    responder_note: str | None = None
    incident_id: str | None = None
    created_at: datetime
    updated_at: datetime


class SafetyIncident(Contract):
    incident_id: str
    parent_id: str
    trigger: Literal["fall.suspected", "parent_message"]
    source: str
    data_mode: DataMode
    confidence: float | None = Field(
        default=None, description="Detector output score, not a clinical probability"
    )
    status: IncidentStatus
    escalation_reason: EscalationReason | None
    resolution: IncidentResolution | None
    no_responder_available: bool
    check_deadline: datetime | None
    opened_at: datetime
    escalated_at: datetime | None
    resolved_at: datetime | None


class CheckIn(Contract):
    checkin_id: str
    parent_id: str
    summary: str
    mood: Literal["good", "okay", "low", "unknown"]
    energy: Literal["high", "normal", "low", "unknown"]
    created_at: datetime


class TimelineEvent(Contract):
    """Dashboard timeline / real-time stream item (also sent over SSE)."""

    id: int
    parent_id: str
    kind: str = Field(examples=["care_request.updated", "incident.escalated", "checkin.recorded"])
    summary: str
    ref_id: str | None
    data: dict
    created_at: datetime


class MetricTrend(Contract):
    metric: Literal["steps", "resting_heart_rate", "sleep_minutes"]
    baseline: float | None
    recent: float | None
    percent_change: float | None
    direction: Literal["up", "down", "stable", "insufficient_data"]
    notable: bool
    observation: str = Field(description="Plain-language, NON-diagnostic description")


class HealthSummary(Contract):
    parent_id: str
    data_mode: DataMode | Literal["mixed", "none"]
    observations: list[HealthObservation]
    trends: list[MetricTrend]
    suggest_checkin: bool
    disclaimer: str


class ParentOverview(Contract):
    parent: ParentProfile
    viewer_role: PersonRole
    permissions: dict[str, bool]
    latest_checkin: CheckIn | None
    health: HealthSummary | None
    active_incidents: list[SafetyIncident]
    recent_requests: list[CareRequest]
    care_circle: list[CareCircleMember]


class Me(Contract):
    person: PersonSummary
    parent_ids: list[str]


class SystemHealth(Contract):
    status: Literal["ok"]
    environment: str
    commit: str | None = Field(default=None, description="Deployed git commit, if known")
    database: Literal["ok", "error"]
    messaging: Literal["photon", "console"]
    photon_delivery: str
    ai: Literal["gemini", "fallback"]
    demo_mode: bool


# Payloads whose JSON Schemas are exported to packages/contracts/schemas/.
EXPORTED_CONTRACTS: dict[str, type[BaseModel]] = {
    "health_observation": HealthObservation,
    "fall_suspected_event": FallSuspectedEvent,
    "care_request": CareRequest,
    "responder_reply": ResponderReply,
    "safety_incident": SafetyIncident,
    "timeline_event": TimelineEvent,
    "parent_overview": ParentOverview,
    "health_summary": HealthSummary,
}
