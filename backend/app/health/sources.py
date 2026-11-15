"""Operator-only source registration; a browser cannot choose a Tiger subject.

Run with the API's configured database, for example:
python -m app.health.sources link --parent-id PARENT --subject-id SUBJECT \
    --actor-id ACCOUNT_OWNER --consent-confirmed
"""

import argparse

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from app.db import init_db, new_id, session_scope, utcnow, watch_data_session
from app.experience_models import AccessAudit, Account, CareProfile
from app.health.live_models import WatchSourceLink
from app.models import Person


class SourceLinkError(ValueError):
    pass


def authorized_operator_actor(session: Session, *, parent_id: str, actor_id: str) -> Person:
    """Attribute the operator action to an existing real account/care profile."""
    parent = session.get(Person, parent_id)
    actor = session.get(Person, actor_id)
    if parent is None or parent.role != "parent":
        raise SourceLinkError("The parent must already exist in FarCare.")
    if actor is None or session.get(Account, actor_id) is None:
        raise SourceLinkError("The actor must have a registered FarCare account.")
    profile = session.get(CareProfile, parent_id)
    if actor.id != parent.id and (profile is None or profile.owner_id != actor.id):
        raise SourceLinkError(
            "The actor must be the parent or the account that set up their care profile."
        )
    if session.get(Account, parent_id) is None and profile is None:
        raise SourceLinkError(
            "Create a real care profile first; demo seed parents cannot be linked."
        )
    return parent


def source_exists(session: Session, subject_id: str) -> bool:
    with watch_data_session(session) as watch:
        if watch.get_bind().dialect.name != "postgresql":
            raise SourceLinkError(
                "Source registration requires the configured Tiger PostgreSQL database."
            )
        watch.execute(text("SET LOCAL statement_timeout = '3s'"))
        return (
            watch.execute(
                text("""
                SELECT 1 FROM air_health.measurement_versions
                WHERE subject_id = :subject_id AND canonical_eligible
                  AND source IN ('ble', 'google_health')
                LIMIT 1
            """),
                {"subject_id": subject_id},
            ).scalar()
            is not None
        )


def link_source(
    session: Session,
    *,
    parent_id: str,
    subject_id: str,
    actor_id: str,
    consent_confirmed: bool,
) -> WatchSourceLink:
    if not consent_confirmed:
        raise SourceLinkError(
            "Confirm that the wearer authorized this account to use their sensor data."
        )
    subject_id = subject_id.strip()
    if not subject_id or len(subject_id) > 256 or any(ord(c) < 32 for c in subject_id):
        raise SourceLinkError("An explicit valid collector subject ID is required.")
    authorized_operator_actor(session, parent_id=parent_id, actor_id=actor_id)
    link = session.get(WatchSourceLink, parent_id)
    if link is not None and link.subject_id != subject_id:
        raise SourceLinkError(
            "This parent already has another source. Review its history before changing identity."
        )
    other = session.exec(
        select(WatchSourceLink).where(WatchSourceLink.subject_id == subject_id)
    ).first()
    if other is not None and other.parent_id != parent_id:
        raise SourceLinkError("This source is already assigned to another parent.")
    if not source_exists(session, subject_id):
        raise SourceLinkError("No eligible measurements exist for that collector subject.")
    if link is not None and link.enabled:
        return link
    link = link or WatchSourceLink(parent_id=parent_id, subject_id=subject_id)
    link.enabled = True
    link.consent_confirmed_at = utcnow()
    link.next_sync_at = None
    link.sync_status = "pending"
    session.add(link)
    session.add(
        AccessAudit(
            id=new_id("audit"), actor_id=actor_id, parent_id=parent_id, action="watch_source.linked"
        )
    )
    session.flush()
    return link


def disable_source(session: Session, *, parent_id: str, actor_id: str) -> None:
    authorized_operator_actor(session, parent_id=parent_id, actor_id=actor_id)
    link = session.get(WatchSourceLink, parent_id)
    if link is None or not link.enabled:
        return
    link.enabled = False
    session.add(link)
    session.add(
        AccessAudit(
            id=new_id("audit"),
            actor_id=actor_id,
            parent_id=parent_id,
            action="watch_source.disabled",
        )
    )
    session.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("link", "disable"))
    parser.add_argument("--parent-id", required=True)
    parser.add_argument("--actor-id", required=True)
    parser.add_argument("--subject-id")
    parser.add_argument("--consent-confirmed", action="store_true")
    args = parser.parse_args()
    if args.action == "link" and not args.subject_id:
        parser.error("link requires --subject-id")
    try:
        init_db()
        with session_scope() as session:
            if args.action == "link":
                link_source(
                    session,
                    parent_id=args.parent_id,
                    subject_id=args.subject_id,
                    actor_id=args.actor_id,
                    consent_confirmed=args.consent_confirmed,
                )
            else:
                disable_source(session, parent_id=args.parent_id, actor_id=args.actor_id)
    except SourceLinkError as exc:
        print(str(exc))
        return 2
    except SQLAlchemyError:
        # Driver exceptions may include connection details or SQL parameters.
        print("Source registration failed. Check the database connection and collector schema.")
        return 1
    print("Watch source linked." if args.action == "link" else "Watch source disabled.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
