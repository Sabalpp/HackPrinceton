"""Standalone durable worker: ``python -m app.worker``.

Each unit holds the singleton AgentLease row lock and commits business changes
and outbound intents together. PostgreSQL releases the lock on disconnect; no
expiring lease can authorize a second worker while the first still holds it.
SQLite uses BEGIN IMMEDIATE for local development/test transactions only.

Production webhooks enqueue only. Do not run a legacy inline-processing API
alongside this worker during an upgrade. No live provider is contacted by tests.
"""

import argparse
import json
import logging
import signal
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import Session, select

from app.config import get_settings
from app.db import get_engine, init_db, session_scope, utcnow
from app.integrations import delivery
from app.integrations.photon.inbound import InboundText
from app.models import InboundMessageRow
from app.runtime_models import AgentLease, DeliveryJob, InboundRetry
from app.services import care_requests, conversation, proactive, safety

log = logging.getLogger(__name__)
LEASE_ID = "farcare-worker"
MAX_INBOUND_ATTEMPTS = 3


class WorkerBusy(Exception):
    """Another worker holds the database mutex; this is normal standby behavior."""


def ensure_lease() -> None:
    try:
        with session_scope() as session:
            if session.get(AgentLease, LEASE_ID) is None:
                session.add(AgentLease(id=LEASE_ID))
    except IntegrityError:
        # Another process created the singleton between SELECT and INSERT.
        pass


def _locked_error(exc: OperationalError) -> bool:
    original = exc.orig
    return getattr(original, "sqlstate", None) == "55P03" or getattr(
        original, "sqlite_errorcode", None
    ) in (5, 6)


@contextmanager
def locked_session(
    owner_id: str, *, track_heartbeat: bool = True
) -> Iterator[tuple[Session, AgentLease]]:
    """Hold the mutex for the entire unit, including the final database commit."""
    with Session(get_engine(), expire_on_commit=False) as session:
        try:
            if get_engine().dialect.name == "sqlite":
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            lease = session.exec(
                select(AgentLease).where(AgentLease.id == LEASE_ID).with_for_update(nowait=True)
            ).one()
            if track_heartbeat:
                lease.owner_id = owner_id
                lease.heartbeat_at = utcnow()
                lease.last_error_code = None
            yield session, lease
            if track_heartbeat:
                lease.last_success_at = utcnow()
                session.add(lease)
            session.commit()
        except OperationalError as exc:
            session.rollback()
            if _locked_error(exc):
                raise WorkerBusy from None
            raise
        except BaseException:
            session.rollback()
            raise


def inbound_unit(owner_id: str) -> str:
    with locked_session(owner_id) as (session, lease):
        row = session.exec(
            select(InboundMessageRow)
            .outerjoin(InboundRetry, InboundRetry.id == InboundMessageRow.id)
            .where(
                InboundMessageRow.status == "pending",
                or_(InboundRetry.id.is_(None), InboundRetry.next_attempt_at <= utcnow()),  # type: ignore[attr-defined,operator]
            )
            .order_by(InboundMessageRow.received_at, InboundMessageRow.id)
            .limit(1)
        ).first()
        if row is None:
            return "empty"
        # Flush attempts outside the savepoint: a handler exception rolls back
        # its business effects while preserving this bounded retry accounting.
        row.attempts += 1
        session.add(row)
        session.flush()
        try:
            with session.begin_nested():
                outcome = conversation.handle(session, InboundText(**row.payload))
                session.flush()
        except Exception:
            row.status = "failed" if row.attempts >= MAX_INBOUND_ATTEMPTS else "pending"
            row.error = "handler_failed"
            lease.last_error_code = "inbound_handler_failed"
            retry = session.get(InboundRetry, row.id)
            if retry is None:
                retry = InboundRetry(id=row.id, next_attempt_at=utcnow())
            retry.next_attempt_at = utcnow() + timedelta(seconds=delivery.retry_delay(row.attempts))
            session.add(retry)
        else:
            row.status = outcome
            row.error = None
            retry = session.get(InboundRetry, row.id)
            if retry is not None:
                session.delete(retry)
        session.add(row)
        return "processed"


def maintenance_unit(owner_id: str) -> str:
    with locked_session(owner_id) as (session, _lease):
        # Do not overtake an already-persisted OK/HELP with a timeout escalation.
        # Queue age must be monitored: sustained backlog delays timer work.
        pending = session.exec(
            select(InboundMessageRow.id)
            .where(InboundMessageRow.status.in_(["pending", "processing"]))  # type: ignore[attr-defined]
            .limit(1)
        ).first()
        if pending is not None:
            return "deferred"
        safety.check_overdue(session)
        care_requests.expire_overdue(session)
        proactive.run(session)
        return "completed"


def delivery_unit(owner_id: str) -> str:
    with locked_session(owner_id) as (session, _lease):
        job = session.exec(
            select(DeliveryJob)
            .where(DeliveryJob.status == "pending", DeliveryJob.next_attempt_at <= utcnow())
            .order_by(DeliveryJob.next_attempt_at, DeliveryJob.id)
            .limit(1)
        ).first()
        if job is None:
            return "empty"
        return delivery.dispatch(session, job)


def run_cycle(owner_id: str | None = None) -> dict[str, int | bool]:
    """Bound the number of jobs per cycle. No services run without the DB mutex."""
    ensure_lease()
    owner_id = owner_id or str(uuid4())
    result: dict[str, int | bool] = {"inbound": 0, "delivery": 0, "busy": False}
    try:
        for _ in range(get_settings().worker_batch_size):
            if inbound_unit(owner_id) == "empty":
                break
            result["inbound"] += 1
        result["maintenance_deferred"] = maintenance_unit(owner_id) == "deferred"
        for _ in range(get_settings().worker_batch_size):
            if delivery_unit(owner_id) == "empty":
                break
            result["delivery"] += 1
    except WorkerBusy:
        result["busy"] = True
    return result


def runtime_status(session: Session) -> dict:
    """Read-only operational counts; never include recipients, bodies, or secrets."""
    now = utcnow()
    lease = session.get(AgentLease, LEASE_ID)
    last = lease.last_success_at if lease else None

    def count(model, state: str) -> int:
        return session.exec(
            select(func.count()).select_from(model).where(model.status == state)
        ).one()

    oldest = session.exec(
        select(func.min(InboundMessageRow.received_at)).where(
            InboundMessageRow.status.in_(["pending", "processing"])  # type: ignore[attr-defined]
        )
    ).one()
    return {
        "worker_healthy": bool(
            last and (now - last).total_seconds() <= get_settings().worker_heartbeat_stale_seconds
        ),
        "last_success_at": last.isoformat() if last else None,
        "inbound_pending": count(InboundMessageRow, "pending"),
        "inbound_processing_legacy": count(InboundMessageRow, "processing"),
        "inbound_failed": count(InboundMessageRow, "failed"),
        "oldest_inbound_age_seconds": max(0, int((now - oldest).total_seconds()))
        if oldest
        else None,
        "delivery_pending": count(DeliveryJob, "pending"),
        "delivery_failed": count(DeliveryJob, "failed"),
        "delivery_cancelled": count(DeliveryJob, "cancelled"),
        "delivery_accepted": count(DeliveryJob, "accepted"),
        "delivery_receipts_verified": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Run one bounded cycle and exit")
    parser.add_argument(
        "--check", action="store_true", help="Read worker health; never process jobs"
    )
    args = parser.parse_args(argv)
    settings = get_settings()
    if settings.app_env == "production" and get_engine().dialect.name != "postgresql":
        parser.error("Production worker requires PostgreSQL")
    if args.check:
        with session_scope() as session:
            status = runtime_status(session)
        print(json.dumps(status))
        return 0 if status["worker_healthy"] else 1
    init_db()
    logging.basicConfig(level=logging.INFO)
    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda _signum, _frame: stop.set())
    owner_id = str(uuid4())
    while not stop.is_set():
        try:
            run_cycle(owner_id)
        except Exception:
            # Exception bodies can contain DSNs/provider data. Persisted queue
            # state survives this failure; a supervisor can restart the process.
            log.error("Worker cycle failed; database or processing unavailable")
            if args.once:
                return 1
        if args.once:
            return 0
        stop.wait(settings.worker_interval_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
