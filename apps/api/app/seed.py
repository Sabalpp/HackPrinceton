"""Synthetic demo data (no real people). Idempotent: safe to run on every boot.

Real phone numbers / iMessage emails come ONLY from env (DEMO_*_HANDLE) and tokens from
DEMO_*_TOKEN, so nothing personal is committed to git. Each role needs a DISTINCT handle.
"""

from sqlmodel import Session, select

from app.auth import hash_token
from app.config import get_settings
from app.health import service as health
from app.integrations.photon.inbound import normalize_handle
from app.models import CareCircleMember, FamilyLink, HealthObservationRow, Person
from app.schemas.contracts import AssistanceCategory as C

PARENT_ID = "parent_001"
CHILD_ID = "child_001"
RESPONDER_ID = "responder_001"
RESPONDER2_ID = "responder_002"
ADMIN_ID = "admin_001"


def _upsert_person(session: Session, pid: str, **fields) -> Person:
    person = session.get(Person, pid)
    if person is None:
        person = Person(id=pid, **fields)
    else:
        for k, v in fields.items():
            setattr(person, k, v)
    session.add(person)
    session.flush()
    return person


def seed(session: Session) -> None:
    st = get_settings()

    def h(value: str) -> str | None:
        return normalize_handle(value) if value else None

    # Clear handles first so swapping numbers between roles never hits the unique index.
    for pid in (PARENT_ID, CHILD_ID, RESPONDER_ID, RESPONDER2_ID):
        existing = session.get(Person, pid)
        if existing is not None and existing.handle is not None:
            existing.handle = None
            session.add(existing)
    session.flush()

    _upsert_person(
        session,
        PARENT_ID,
        role="parent",
        display_name="Sita",
        timezone="Asia/Kathmandu",
        emergency_number=st.demo_emergency_number or None,
        handle=h(st.demo_parent_handle),
        api_token_hash=hash_token(st.demo_parent_token),
    )
    _upsert_person(
        session,
        CHILD_ID,
        role="child",
        display_name="Aarav",
        timezone="America/New_York",
        handle=h(st.demo_child_handle),
        api_token_hash=hash_token(st.demo_child_token),
    )
    _upsert_person(
        session,
        RESPONDER_ID,
        role="responder",
        display_name="Asha",
        timezone="Asia/Kathmandu",
        handle=h(st.demo_responder_handle),
    )
    _upsert_person(
        session,
        RESPONDER2_ID,
        role="responder",
        display_name="Ram",
        timezone="Asia/Kathmandu",
        handle=h(st.demo_responder2_handle),
    )
    _upsert_person(
        session,
        ADMIN_ID,
        role="admin",
        display_name="Demo Admin",
        api_token_hash=hash_token(st.demo_admin_token),
    )

    if not session.exec(select(FamilyLink).where(FamilyLink.parent_id == PARENT_ID)).first():
        session.add(
            FamilyLink(
                parent_id=PARENT_ID,
                member_id=CHILD_ID,
                relationship="son",
                parent_label="Mom",
                share_health=True,
                share_checkins=True,
                receives_emergency_alerts=True,
            )
        )
    if not session.exec(
        select(CareCircleMember).where(CareCircleMember.parent_id == PARENT_ID)
    ).first():
        session.add(
            CareCircleMember(
                parent_id=PARENT_ID,
                responder_id=RESPONDER_ID,
                relationship="neighbor",
                categories=[
                    C.grocery_assistance,
                    C.household_help,
                    C.companionship,
                    C.meal_assistance,
                    C.medication_pickup,
                ],
                emergency_contact=True,
                priority=10,
            )
        )
        session.add(
            CareCircleMember(
                parent_id=PARENT_ID,
                responder_id=RESPONDER2_ID,
                relationship="family friend",
                categories=[C.transportation, C.grocery_assistance, C.medication_pickup],
                emergency_contact=True,
                priority=20,
            )
        )
    if (
        st.seed_health_data
        and not session.exec(
            select(HealthObservationRow).where(HealthObservationRow.parent_id == PARENT_ID)
        ).first()
    ):
        health.seed_simulated(session, PARENT_ID)
    session.flush()
