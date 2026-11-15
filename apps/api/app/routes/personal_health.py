"""Dated cuff readings and transparent learning; never a diagnostic service."""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, select

from app import accounts
from app.auth import current_person, resolve_parent_access
from app.db import get_session, new_id, utcnow
from app.experience_models import AccessAudit, BloodPressureReading, PersonalReference
from app.health import live, personal
from app.health.service import recent
from app.integrations.messaging import send_to_person
from app.models import FamilyLink, Person
from app.mutations import action_session
from app.routes.experience import act, agent_status, role
from app.schemas.experience import (
    BPAssessment,
    BPReadingIn,
    BPReadingInfo,
    EvidenceSource,
    PersonalHealthReport,
    PersonalMetric,
    ReferenceIn,
    ReferenceInfo,
    SensorHealthReport,
)
from app.services import timeline

router = APIRouter(prefix="/api", tags=["personal health"])


@router.get("/parents/{parent_id}/sensor-health", response_model=SensorHealthReport | None)
def get_sensor_health(
    parent_id: str,
    viewer: Person = Depends(current_person),
    session: Session = Depends(get_session),
):
    access = resolve_parent_access(session, viewer, parent_id)
    if not access.share_health:
        return None
    # Nearby helpers receive task details, never a sensor-health feed.
    if viewer.role not in ("parent", "child", "family", "admin"):
        raise HTTPException(404, "Parent not found")
    parent = session.get(Person, parent_id)
    assert parent
    return live.read_sensor_health(session, parent)


def bp_record(row: BloodPressureReading) -> dict:
    return row.model_dump()


def bp_info(
    row: BloodPressureReading, previous: BloodPressureReading | None = None
) -> BPReadingInfo:
    assessment = personal.assess_bp(
        bp_record(row), previous_reading=bp_record(previous) if previous else None
    )
    return BPReadingInfo(
        reading_id=row.reading_id,
        systolic=row.systolic,
        diastolic=row.diastolic,
        measured_at=row.measured_at,
        source=row.source,
        data_mode=row.data_mode,
        device_validated=row.device_validated,
        symptoms_checked=row.symptoms_checked,
        symptoms=row.symptoms,
        assessment=BPAssessment.model_validate(assessment),
    )


def latest_references(session: Session, parent_id: str) -> dict[str, PersonalReference]:
    rows = session.exec(
        select(PersonalReference)
        .where(PersonalReference.parent_id == parent_id)
        .order_by(PersonalReference.created_at.desc())
    ).all()
    result = {}
    for row in rows:
        result.setdefault(row.metric, row)
    return result


def reference_info(row: PersonalReference) -> ReferenceInfo:
    return ReferenceInfo(
        metric=row.metric, value=row.value, note=row.note, declared_at=row.created_at
    )


@router.get("/parents/{parent_id}/personal-health", response_model=PersonalHealthReport | None)
def get_personal_health(
    parent_id: str,
    viewer: Person = Depends(current_person),
    session: Session = Depends(get_session),
):
    access = resolve_parent_access(session, viewer, parent_id)
    if not access.share_health:
        return None
    parent = session.get(Person, parent_id)
    assert parent
    clock = utcnow()
    refs = latest_references(session, parent_id)
    bps = session.exec(
        select(BloodPressureReading)
        .where(
            BloodPressureReading.parent_id == parent_id,
            BloodPressureReading.measured_at >= clock - timedelta(days=29),
        )
        .order_by(BloodPressureReading.measured_at, BloodPressureReading.created_at)
    ).all()
    daily = recent(session, parent_id, days=35)
    metrics = []
    for metric in ("steps", "resting_heart_rate", "sleep_minutes", "systolic", "diastolic"):
        samples = []
        if metric in ("systolic", "diastolic"):
            samples = [
                {**bp_record(row), "observed_at": row.measured_at, "value": getattr(row, metric)}
                for row in bps
            ]
        else:
            samples = [
                {
                    "value": getattr(row, metric),
                    "observed_at": row.timestamp,
                    "local_date": row.timestamp.date().isoformat(),
                    "kind": "daily",
                    "source": "google_health" if row.source == "fitbit" else row.source,
                    "source_stream": "farcare_daily:" + row.source,
                    "data_mode": row.data_mode,
                }
                for row in daily
                if getattr(row, metric) is not None
            ]
        reference = {"value": refs[metric].value} if metric in refs else None
        result = personal.build_baseline(
            samples,
            metric,
            now=clock,
            timezone_name=parent.timezone,
            declared_reference=reference,
            current=max(samples, key=lambda sample: sample["observed_at"]) if samples else None,
        )
        declared = result.get("declared_reference")
        result["declared_reference"] = (
            declared.get("value") if isinstance(declared, dict) else declared
        )
        metrics.append(PersonalMetric.model_validate(result))
    readings = [bp_info(row, bps[index - 1] if index else None) for index, row in enumerate(bps)]
    return PersonalHealthReport(
        parent_id=parent_id,
        metrics=metrics,
        references=[reference_info(row) for row in refs.values()],
        blood_pressure=list(reversed(readings[-50:])),
        agent=agent_status(session, viewer),
        generated_at=clock,
        evidence=[
            EvidenceSource(
                title="American Heart Association: home BP guidance",
                url=personal.EVIDENCE["home_bp"],
            ),
            EvidenceSource(
                title="NICE: hypertension measurement guidance", url=personal.EVIDENCE["nice"]
            ),
            EvidenceSource(
                title="FDA: blood-pressure measurement devices", url=personal.EVIDENCE["device"]
            ),
        ],
        explanation="Personal references are user-declared and provisional. Seven qualified completed days support a descriptive baseline; this is not clinical calibration. Missing readings stay missing. BP guidance is for adults who are not pregnant; symptoms and urgent BP guidance cannot be overridden by a personal reference.",
    )


@router.post("/parent/blood-pressure", response_model=BPReadingInfo, status_code=201)
def save_bp(
    body: BPReadingIn,
    request: Request,
    person: Person = Depends(current_person),
    session: Session = Depends(action_session),
):
    role(person, "parent")

    def apply():
        if (
            not utcnow() - timedelta(hours=24)
            <= body.measured_at
            <= utcnow() + timedelta(minutes=5)
        ):
            raise HTTPException(
                422, "Enter a reading taken within the last 24 hours; check its date and time."
            )
        previous = session.exec(
            select(BloodPressureReading)
            .where(
                BloodPressureReading.parent_id == person.id,
                BloodPressureReading.measured_at < body.measured_at,
                BloodPressureReading.measured_at >= body.measured_at - timedelta(hours=1),
            )
            .order_by(BloodPressureReading.measured_at.desc())
            .limit(1)
        ).first()
        row = BloodPressureReading(
            reading_id=new_id("bp"),
            parent_id=person.id,
            systolic=body.systolic,
            diastolic=body.diastolic,
            measured_at=body.measured_at,
            symptoms=list(body.symptoms),
            symptoms_checked=body.symptoms_checked,
            device_validated=body.device_validated,
            adult_nonpregnant=body.adult_nonpregnant,
            data_mode="simulated"
            if (identity := accounts.cookie_identity(session, request, mutation=False))
            and identity[1].is_demo
            else "live",
        )
        session.add(row)
        result = bp_info(row, previous)
        timeline.record(
            session,
            person.id,
            "health.bp_recorded",
            "A cuff blood-pressure reading was recorded.",
            ref_id=row.reading_id,
            visibility=timeline.HEALTH,
        )
        if result.assessment.level in ("emergency", "contact_clinician"):
            # Share only with explicitly authorized family. No neighbor dispatch
            # or emergency-service calls are generated from an algorithm result.
            links = session.exec(
                select(FamilyLink, Person).where(
                    FamilyLink.parent_id == person.id,
                    FamilyLink.share_health == True,  # noqa: E712
                    FamilyLink.member_id == Person.id,
                )
            ).all()
            for link, member in links:
                send_to_person(
                    session,
                    member,
                    f"FarCare: {link.parent_label or person.display_name} recorded a BP reading that needs attention. "
                    "Please check in. The parent app shows guidance; do not wait for an app reply if urgent symptoms are present.",
                    f"bp:{row.reading_id}:{member.id}",
                    parent_id=person.id,
                    visibility="health",
                )
        return result

    return act(session, person, "blood-pressure", body, apply)


@router.post("/parents/{parent_id}/reference", response_model=ReferenceInfo)
def declare_reference(
    parent_id: str,
    body: ReferenceIn,
    person: Person = Depends(current_person),
    session: Session = Depends(action_session),
):
    role(person, "parent")
    if person.id != parent_id:
        raise HTTPException(404, "Parent not found")
    bounds = {
        "steps": (0, 200000),
        "resting_heart_rate": (20, 250),
        "sleep_minutes": (0, 1440),
        "systolic": (50, 300),
        "diastolic": (20, 200),
    }
    low, high = bounds[body.metric]
    if not low <= body.value <= high or (body.metric == "steps" and not body.value.is_integer()):
        raise HTTPException(422, "Check the units and value for this personal reference")
    row = PersonalReference(
        id=new_id("reference"),
        parent_id=parent_id,
        metric=body.metric,
        value=body.value,
        note=body.note,
        actor_id=person.id,
    )
    session.add(row)
    session.add(
        AccessAudit(
            id=new_id("audit"), actor_id=person.id, parent_id=parent_id, action="reference.declared"
        )
    )
    timeline.record(
        session,
        parent_id,
        "health.reference_declared",
        "The parent declared a provisional personal reference.",
        ref_id=row.id,
        visibility=timeline.HEALTH,
    )
    return reference_info(row)
