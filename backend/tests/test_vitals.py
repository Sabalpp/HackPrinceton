"""Heart-rate alert limits and "what's the bpm?" answers. Synthetic people and readings only."""

from datetime import timedelta

import pytest
from sqlmodel import select

from app.config import get_settings
from app.db import session_scope, utcnow
from app.health.live_models import WatchSourceLink
from app.models import FamilyLink, HeartRateAlertRow, OutboundMessage, Person, SafetyIncidentRow
from app.services import vitals
from tests.conftest import say, texts_to
from tests.test_experience import action, demo, get, post

URL = "/api/parents/parent_001/heart-rate-alert"


@pytest.fixture(autouse=True)
def console_only(monkeypatch):
    for key, value in {
        "SEED_HEALTH_DATA": "false",
        "MESSAGING_CHANNEL": "console",
        "PHOTON_DELIVERY": "off",
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def samples(bpm: float | list[float], *, minutes: int = 2, every: int = 5, end_ago: int = 0):
    now = utcnow()
    count = minutes * 60 // every + 1
    values = bpm if isinstance(bpm, list) else [bpm] * count
    return now, [
        (now - timedelta(seconds=end_ago + every * (len(values) - 1 - i)), float(v))
        for i, v in enumerate(values)
    ]


def rule(**fields) -> HeartRateAlertRow:
    base = {"parent_id": "parent_001", "updated_by": "child_001", "high_bpm": 120, "low_bpm": 45}
    return HeartRateAlertRow(**{**base, **fields})


def test_crossing_needs_the_whole_window_fresh_and_without_gaps():
    now, high = samples(130)
    assert vitals.crossing(high, rule(), now) == ("above", 130.0)
    now, low = samples(40)
    assert vitals.crossing(low, rule(), now) == ("below", 40.0)
    # One spike, a dip back inside the limits, stale data, or a silent gap never trigger.
    now, spike = samples([80] * 24 + [160])
    assert vitals.crossing(spike, rule(), now) is None
    now, mixed = samples([130] * 12 + [110] + [130] * 12)
    assert vitals.crossing(mixed, rule(), now) is None
    now, stale = samples(130, end_ago=90)
    assert vitals.crossing(stale, rule(), now) is None
    now, gappy = samples(130)
    assert vitals.crossing(gappy[:5] + gappy[15:], rule(), now) is None


def test_family_with_health_sharing_can_set_limits_and_helpers_cannot(client):
    child = demo(client, "child")
    saved = post(client, child, URL, action(high_bpm=120, low_bpm=45, sustain_minutes=3))
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert (body["enabled"], body["high_bpm"], body["low_bpm"], body["sustain_minutes"]) == (
        True,
        120,
        45,
        3,
    )
    assert get(client, child, URL).json()["updated_by_name"]

    invalid = post(client, child, URL, action(high_bpm=60, low_bpm=70))
    assert invalid.status_code == 422

    helper = demo(client, "responder")
    assert get(client, helper, URL).status_code == 404
    assert post(client, helper, URL, action(high_bpm=130)).status_code == 404

    with session_scope() as s:
        link = s.exec(select(FamilyLink).where(FamilyLink.member_id == child["id"])).first()
        link.share_health = False
        s.add(link)
    assert get(client, child, URL).status_code == 403


def test_crossing_texts_family_with_numbers_and_asks_parent_once(client, monkeypatch):
    with session_scope() as s:
        s.add(WatchSourceLink(parent_id="parent_001", subject_id="subject-test"))
        vitals.save_alert(
            s,
            s.get(Person, "parent_001"),
            s.get(Person, "child_001"),
            high_bpm=120,
            low_bpm=None,
            check_on_parent=True,
        )
    now, high = samples(132)
    monkeypatch.setattr(vitals, "recent_heart_rate", lambda *a, **k: high)

    with session_scope() as s:
        assert vitals.check_alerts(s, now=now) == 1
    with session_scope() as s:
        family = [
            m.text
            for m in s.exec(select(OutboundMessage).where(OutboundMessage.person_id == "child_001"))
        ]
        assert any("above your alert limit of 120 bpm" in t and "132 bpm" in t for t in family)
        helpers = [
            m.text
            for m in s.exec(
                select(OutboundMessage).where(OutboundMessage.person_id.like("responder%"))
            )  # type: ignore[union-attr]
        ]
        assert not any("bpm" in t for t in helpers)  # nearby helpers never get health numbers
        incident = s.exec(
            select(SafetyIncidentRow).where(SafetyIncidentRow.parent_id == "parent_001")
        ).first()
        assert incident is not None and incident.status == "checking"

    # Within the cooldown nothing repeats, even though the reading is still high.
    with session_scope() as s:
        assert vitals.check_alerts(s, now=now + timedelta(minutes=5)) == 0


def test_sms_questions_get_real_numbers_and_can_set_a_limit(client, monkeypatch):
    now = utcnow()
    readings = [
        vitals.Reading(
            "Heart rate", 72, "bpm", at=now - timedelta(seconds=5), source="watch", live=True
        ),
        vitals.Reading("Resting heart rate", 64, "bpm", day=now.date() - timedelta(days=1)),
        vitals.Reading("Blood oxygen", 96.4, "%", day=now.date() - timedelta(days=1)),
    ]
    monkeypatch.setattr(vitals, "latest_readings", lambda *a, **k: readings)

    son = " ".join(texts_to(say(client, "child_001", "what is mom's bpm?"), "child"))
    assert "72 bpm" in son and "64 bpm" in son and "96.4%" not in son
    me = " ".join(texts_to(say(client, "parent_001", "what's my heart rate?"), "parent"))
    assert "72 bpm" in me
    helper = " ".join(texts_to(say(client, "responder_001", "what's Sita's bpm?"), "responder"))
    assert "72" not in helper

    reply = " ".join(
        texts_to(say(client, "child_001", "text me if her bpm is above 125 for 3 minutes"), "child")
    )
    assert "above 125 bpm for 3 min" in reply
    with session_scope() as s:
        row = s.get(HeartRateAlertRow, "parent_001")
        assert (row.high_bpm, row.sustain_minutes, row.updated_by) == (125, 3, "child_001")
