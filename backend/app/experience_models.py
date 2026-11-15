"""Additive account, consent and personal-health tables; no existing columns change."""

from datetime import datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from app.db import dt_column, utcnow


class Account(SQLModel, table=True):
    __tablename__ = "accounts"
    person_id: str = Field(primary_key=True, foreign_key="people.id")
    email: str = Field(unique=True, index=True)
    password_hash: str
    created_at: datetime = Field(default_factory=utcnow, sa_column=dt_column(nullable=False))


class LoginSession(SQLModel, table=True):
    __tablename__ = "login_sessions"
    token_hash: str = Field(primary_key=True)
    person_id: str = Field(foreign_key="people.id", index=True)
    csrf_token: str
    is_demo: bool = False
    expires_at: datetime = Field(sa_column=dt_column(nullable=False, index=True))
    created_at: datetime = Field(default_factory=utcnow, sa_column=dt_column(nullable=False))


class AuthAttempt(SQLModel, table=True):
    __tablename__ = "auth_attempts"
    id: str = Field(primary_key=True)
    bucket: str = Field(index=True)
    created_at: datetime = Field(
        default_factory=utcnow, sa_column=dt_column(nullable=False, index=True)
    )


class CareProfile(SQLModel, table=True):
    __tablename__ = "care_profiles"
    parent_id: str = Field(primary_key=True, foreign_key="people.id")
    owner_id: str = Field(foreign_key="people.id")
    country_code: str = "OTHER"


class Invitation(SQLModel, table=True):
    __tablename__ = "invitations"
    token_hash: str = Field(primary_key=True)
    person_id: str = Field(foreign_key="people.id")
    parent_id: str = Field(foreign_key="people.id")
    created_by_id: str = Field(foreign_key="people.id")
    role: str
    consumed_at: datetime | None = Field(default=None, sa_column=dt_column())
    expires_at: datetime = Field(sa_column=dt_column(nullable=False))


class BloodPressureReading(SQLModel, table=True):
    __tablename__ = "blood_pressure_readings"
    reading_id: str = Field(primary_key=True)
    parent_id: str = Field(foreign_key="people.id", index=True)
    measured_at: datetime = Field(sa_column=dt_column(nullable=False, index=True))
    systolic: int
    diastolic: int
    device_validated: bool | None = None
    symptoms_checked: bool = False
    adult_nonpregnant: bool = False
    symptoms: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    data_mode: str = "live"
    source: str = "manual_cuff"
    source_stream: str = "manual_cuff"
    created_at: datetime = Field(default_factory=utcnow, sa_column=dt_column(nullable=False))


class PersonalReference(SQLModel, table=True):
    __tablename__ = "personal_references"
    id: str = Field(primary_key=True)
    parent_id: str = Field(foreign_key="people.id", index=True)
    metric: str
    value: float
    note: str = ""
    actor_id: str = Field(foreign_key="people.id")
    created_at: datetime = Field(default_factory=utcnow, sa_column=dt_column(nullable=False))


class ActionReceipt(SQLModel, table=True):
    __tablename__ = "action_receipts"
    id: str = Field(primary_key=True)
    person_id: str = Field(foreign_key="people.id")
    operation: str
    payload_hash: str
    result: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=utcnow, sa_column=dt_column(nullable=False))


class AccessAudit(SQLModel, table=True):
    __tablename__ = "access_audits"
    id: str = Field(primary_key=True)
    actor_id: str = Field(foreign_key="people.id", index=True)
    parent_id: str | None = Field(default=None, index=True)
    action: str
    created_at: datetime = Field(default_factory=utcnow, sa_column=dt_column(nullable=False))
