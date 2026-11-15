"""Trusted source mappings and durable synchronization state, never browser input."""

from datetime import datetime

from sqlmodel import Field, SQLModel

from app.db import dt_column, utcnow


class WatchSourceLink(SQLModel, table=True):
    __tablename__ = "watch_source_links"

    parent_id: str = Field(primary_key=True, foreign_key="people.id")
    subject_id: str = Field(unique=True, index=True)
    enabled: bool = True
    consent_confirmed_at: datetime = Field(
        default_factory=utcnow, sa_column=dt_column(nullable=False)
    )
    created_at: datetime = Field(default_factory=utcnow, sa_column=dt_column(nullable=False))
    next_sync_at: datetime | None = Field(default=None, sa_column=dt_column(index=True))
    last_sync_at: datetime | None = Field(default=None, sa_column=dt_column())
    last_digest: str | None = None
    sync_status: str = "pending"
