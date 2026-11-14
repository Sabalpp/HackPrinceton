"""Database tables. Ownership: Sumit (core) — except HealthObservationRow (Sabal)."""

from datetime import datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from app.db import dt_column, utcnow


class Person(SQLModel, table=True):
    __tablename__ = "people"

    id: str = Field(primary_key=True)
    role: str = Field(index=True)  # PersonRole
    display_name: str
    handle: str | None = Field(default=None, unique=True, index=True)  # E.164 or iMessage email
    timezone: str = "UTC"
    emergency_number: str | None = None  # parents: local emergency services number
    api_token_hash: str | None = Field(default=None, index=True)
    # Photon routing, learned from the person's first inbound message (= opt-in).
    photon_space_id: str | None = None
    photon_agent_user_id: str | None = None
    reachable_since: datetime | None = Field(default=None, sa_column=dt_column())
    created_at: datetime = Field(default_factory=utcnow, sa_column=dt_column(nullable=False))


class FamilyLink(SQLModel, table=True):
    """A distant family member (child) linked to a parent, with parent-granted permissions."""

    __tablename__ = "family_links"

    id: int | None = Field(default=None, primary_key=True)
    parent_id: str = Field(foreign_key="people.id", index=True)
    member_id: str = Field(foreign_key="people.id", index=True)
    relationship: str  # e.g. "son"
    parent_label: str = ""  # how this member refers to the parent in messages, e.g. "Mom"
    share_health: bool = True
    share_checkins: bool = False
    receives_emergency_alerts: bool = True


class CareCircleMember(SQLModel, table=True):
    """A trusted nearby responder the parent has pre-approved."""

    __tablename__ = "care_circle"

    id: int | None = Field(default=None, primary_key=True)
    parent_id: str = Field(foreign_key="people.id", index=True)
    responder_id: str = Field(foreign_key="people.id", index=True)
    relationship: str  # e.g. "neighbor"
    categories: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    emergency_contact: bool = False
    priority: int = 100  # lower = asked first
    active: bool = True


class CareRequestRow(SQLModel, table=True):
    __tablename__ = "care_requests"

    id: str = Field(primary_key=True)
    parent_id: str = Field(foreign_key="people.id", index=True)
    request_type: str
    details: str = ""
    requested_time: str | None = None
    status: str = Field(index=True)
    responder_id: str | None = Field(default=None, foreign_key="people.id", index=True)
    parent_approved: bool = False
    approval_basis: str | None = None
    origin: str = "conversation"
    incident_id: str | None = Field(default=None, index=True)
    requested_by_id: str | None = Field(default=None, foreign_key="people.id")
    eta_minutes: int | None = None
    responder_note: str | None = None
    attempted_responder_ids: list[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    created_at: datetime = Field(default_factory=utcnow, sa_column=dt_column(nullable=False))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=dt_column(nullable=False))
    status_changed_at: datetime = Field(default_factory=utcnow, sa_column=dt_column(nullable=False))


class SafetyIncidentRow(SQLModel, table=True):
    __tablename__ = "safety_incidents"

    id: str = Field(primary_key=True)
    parent_id: str = Field(foreign_key="people.id", index=True)
    trigger: str
    source: str
    data_mode: str
    confidence: float | None = None
    status: str = Field(index=True)
    escalation_reason: str | None = None
    resolution: str | None = None
    no_responder_available: bool = False
    check_deadline: datetime | None = Field(default=None, sa_column=dt_column())
    opened_at: datetime = Field(default_factory=utcnow, sa_column=dt_column(nullable=False))
    escalated_at: datetime | None = Field(default=None, sa_column=dt_column())
    resolved_at: datetime | None = Field(default=None, sa_column=dt_column())


class FallEventRecord(SQLModel, table=True):
    """Every received fall event, keyed by event_id so redelivery is idempotent."""

    __tablename__ = "fall_events"

    event_id: str = Field(primary_key=True)
    parent_id: str = Field(index=True)
    incident_id: str | None = None
    duplicate: bool = False
    payload: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    received_at: datetime = Field(default_factory=utcnow, sa_column=dt_column(nullable=False))


class HealthObservationRow(SQLModel, table=True):
    """Owner: Sabal. One normalized daily observation."""

    __tablename__ = "health_observations"

    observation_id: str = Field(primary_key=True)
    parent_id: str = Field(foreign_key="people.id", index=True)
    source: str
    data_mode: str
    timestamp: datetime = Field(sa_column=dt_column(nullable=False, index=True))
    steps: int | None = None
    resting_heart_rate: int | None = None
    sleep_minutes: int | None = None


class CheckInRow(SQLModel, table=True):
    __tablename__ = "checkins"

    id: str = Field(primary_key=True)
    parent_id: str = Field(foreign_key="people.id", index=True)
    summary: str
    mood: str = "unknown"
    energy: str = "unknown"
    created_at: datetime = Field(default_factory=utcnow, sa_column=dt_column(nullable=False))


class TimelineEventRow(SQLModel, table=True):
    """Append-only event log. Drives the dashboard timeline and the SSE stream."""

    __tablename__ = "timeline_events"

    id: int | None = Field(default=None, primary_key=True)
    parent_id: str = Field(index=True)
    kind: str
    summary: str
    ref_id: str | None = None
    # family | health | checkin | parent_only — filtered by the viewer's permissions
    visibility: str = "family"
    data: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=utcnow, sa_column=dt_column(nullable=False))


class ConversationTurn(SQLModel, table=True):
    """Persistent conversational context for the agent (per person)."""

    __tablename__ = "conversation_turns"

    id: int | None = Field(default=None, primary_key=True)
    person_id: str = Field(index=True)
    role: str  # user | assistant
    text: str
    created_at: datetime = Field(default_factory=utcnow, sa_column=dt_column(nullable=False))


class InboundMessageRow(SQLModel, table=True):
    """Inbound message queue + dedupe (primary key = Photon message id)."""

    __tablename__ = "inbound_messages"

    id: str = Field(primary_key=True)
    payload: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    status: str = "pending"  # pending | done | ignored | failed
    attempts: int = 0
    error: str | None = None
    received_at: datetime = Field(default_factory=utcnow, sa_column=dt_column(nullable=False))


class OutboundMessage(SQLModel, table=True):
    """Every outbound message, keyed by idempotency key so retries never double-send."""

    __tablename__ = "outbound_messages"

    id: str = Field(primary_key=True)  # idempotency key
    person_id: str | None = Field(default=None, index=True)
    channel: str  # photon | console
    text: str
    status: str  # sent | simulated | unreachable | failed
    provider_message_id: str | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=utcnow, sa_column=dt_column(nullable=False))
