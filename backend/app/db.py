"""Database engine, sessions and helpers.

SQLite is the default for local dev and tests. Production uses PostgreSQL
(e.g. Tiger Data / Timescale Cloud) via DATABASE_URL.
"""

import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, TypeDecorator, text
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from app.config import get_settings

_engine: Engine | None = None


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


class UTCDateTime(TypeDecorator):
    """Stores UTC; always returns timezone-aware UTC datetimes (SQLite drops tzinfo)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        value = value.astimezone(UTC)
        return value.replace(tzinfo=None) if dialect.name == "sqlite" else value

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def dt_column(nullable: bool = True, index: bool = False) -> Column:
    return Column(UTCDateTime(), nullable=nullable, index=index)


def _normalize_url(url: str) -> str:
    # Hosted Postgres providers hand out postgres:// URLs; SQLAlchemy + psycopg3 needs this form.
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        url = _normalize_url(get_settings().database_url)
        if url.startswith("sqlite"):
            _engine = create_engine(url, connect_args={"check_same_thread": False})
        else:
            _engine = create_engine(url, pool_pre_ping=True)
    return _engine


def reset_engine() -> None:
    global _engine, _tiger_engine
    if _engine is not None:
        _engine.dispose()
    _engine = None
    if _tiger_engine is not None:
        _tiger_engine.dispose()
    _tiger_engine = None


_tiger_engine: Engine | None = None


def tiger_engine() -> Engine | None:
    """Separate read-only engine for live watch data, or None to use DATABASE_URL."""
    global _tiger_engine
    url = get_settings().tiger_database_url.strip()
    if not url:
        return None
    if _tiger_engine is None:
        from sqlalchemy import event

        _tiger_engine = create_engine(
            _normalize_url(url),
            pool_pre_ping=True,
            pool_size=2,
            max_overflow=2,
            connect_args={"connect_timeout": 5},
        )

        @event.listens_for(_tiger_engine, "connect")
        def _read_only(dbapi_connection, _record):  # every pooled connection, every time
            cursor = dbapi_connection.cursor()
            cursor.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
            cursor.close()
            dbapi_connection.commit()

    return _tiger_engine


@contextmanager
def watch_data_session(main: Session) -> Iterator[Session]:
    """Where air_health.* watch data is read from.

    With TIGER_DATABASE_URL: a separate read-only Tiger session, so a slow or failed read
    never touches the app's own transaction and nothing can be written to Tiger.
    Without it: the app's own session inside a savepoint (single-database deployments).
    """
    engine = tiger_engine()
    if engine is None:
        with main.begin_nested():
            yield main
        return
    with Session(engine) as tiger:
        try:
            yield tiger
        finally:
            tiger.rollback()


def init_db() -> None:
    from app import (  # noqa: F401  (register tables)
        experience_models,
        models,
        runtime_models,  # noqa: F401  (register worker tables)
    )
    from app.health import live_models  # noqa: F401 (register trusted watch links)
    from app.schema_upgrades import apply_additive_upgrades

    engine = get_engine()
    with engine.begin() as connection:
        if engine.dialect.name == "postgresql":
            # API and worker can start together during a rollout. Serialize schema
            # creation/upgrades across processes; release automatically at commit.
            connection.execute(text("SELECT pg_advisory_xact_lock(734276192)"))
        SQLModel.metadata.create_all(connection)
        apply_additive_upgrades(connection)


@contextmanager
def session_scope() -> Iterator[Session]:
    with Session(get_engine(), expire_on_commit=False) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    with session_scope() as session:
        yield session
