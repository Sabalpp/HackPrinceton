"""Additive runtime tables; business tables and public contracts stay unchanged."""

from datetime import datetime

from sqlmodel import Field, SQLModel

from app.db import dt_column, utcnow


class AgentLease(SQLModel, table=True):
    """A transaction-held worker mutex, with durable health observations.

    PostgreSQL FOR UPDATE is the exclusion mechanism. Timestamps are monitoring
    data, never permission for a second worker to steal an in-flight operation.
    """

    __tablename__ = "agent_leases"

    id: str = Field(primary_key=True)
    owner_id: str | None = None
    heartbeat_at: datetime | None = Field(default=None, sa_column=dt_column())
    last_success_at: datetime | None = Field(default=None, sa_column=dt_column())
    last_error_code: str | None = None


class InboundRetry(SQLModel, table=True):
    """Retry scheduling without changing the existing inbound-message schema."""

    __tablename__ = "inbound_retries"

    id: str = Field(primary_key=True, foreign_key="inbound_messages.id")
    next_attempt_at: datetime = Field(sa_column=dt_column(nullable=False, index=True))


class DeliveryJob(SQLModel, table=True):
    """Committed send intent with an immutable provider request for safe replay.

    accepted means Photon accepted dispatch, not that a person's device received
    or read the message. No delivery receipt is fabricated by this table.
    """

    __tablename__ = "delivery_jobs"

    id: str = Field(primary_key=True, foreign_key="outbound_messages.id")
    provider_key: str = Field(unique=True)
    person_id: str
    space_id: str
    sender_id: str
    text: str
    parent_id: str | None = Field(default=None, index=True)
    visibility: str = "unscoped"
    recipient_role: str | None = None
    account_required: bool = False
    account_created_at: datetime | None = Field(default=None, sa_column=dt_column())
    family_link_id: int | None = None
    care_circle_id: int | None = None
    request_id: str | None = None
    offer_only: bool = False
    require_share_health: bool = False
    require_share_checkins: bool = False
    require_emergency: bool = False
    status: str = Field(default="pending", index=True)
    attempts: int = 0
    next_attempt_at: datetime = Field(
        default_factory=utcnow, sa_column=dt_column(nullable=False, index=True)
    )
    created_at: datetime = Field(default_factory=utcnow, sa_column=dt_column(nullable=False))
    accepted_at: datetime | None = Field(default=None, sa_column=dt_column())
    last_error_code: str | None = None
