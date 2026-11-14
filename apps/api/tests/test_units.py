"""Pure unit tests: reply parsing, fall detector, health trends."""

import pytest

from app.fall_detection import detector, traces
from app.health import simulated
from app.health.trends import compute_trends
from app.services import replies


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("YES", "yes"),
        ("yes please", "yes"),
        ("Ho", "yes"),
        ("ok", "yes"),
        ("No", "no"),
        ("no thanks", "no"),
        ("hoina", "no"),
        ("maybe later?", None),
        ("No problem!", "yes"),
    ],
)
def test_yes_no(text, expected):
    assert replies.parse_yes_no(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("OK", "ok"),
        ("I'm fine, just dropped my phone", "ok"),
        ("thik chu", "ok"),
        ("HELP", "help"),
        ("I need help", "help"),
        ("I fell but I'm ok", "help"),
        ("what?", None),
    ],
)
def test_safety_reply_fails_safe(text, expected):
    assert replies.parse_safety_reply(text) == expected


@pytest.mark.parametrize(
    ("text", "is_emergency"),
    [
        ("I fell and can't get up", True),
        ("chest pain since morning", True),
        ("Can you help me with groceries tomorrow?", False),
        ("It's not an emergency but I need a ride", False),
    ],
)
def test_emergency_detection_is_narrow(text, is_emergency):
    assert replies.looks_like_emergency(text) is is_emergency


@pytest.mark.parametrize(
    ("text", "status", "intent"),
    [
        ("Yes, I can help at 4 PM", "sent", "accepted"),
        ("Sorry, I can't today", "sent", "declined"),
        ("No problem, on my way", "sent", "accepted"),
        ("Done!", "accepted", "completed"),
        ("She needs an ambulance", "accepted", "need_more_help"),
        ("hmm", "sent", None),
    ],
)
def test_responder_reply(text, status, intent):
    assert replies.parse_responder_reply(text, status) == intent


def test_eta_parsing():
    assert replies.parse_eta("yes 15 min") == (15, None)
    assert replies.parse_eta("Yes, I can help at 4 PM") == (None, "4 PM")
    assert replies.parse_eta("in an hour") == (60, None)


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
@pytest.mark.parametrize(
    ("kind", "label"),
    [
        ("walking", "walking"),
        ("sitting", "sitting"),
        ("bending", "bending"),
        ("lying_down", "lying_down"),
        ("fall", "possible_fall"),
        ("recovery", "recovered_after_impact"),
    ],
)
def test_detector_classifies_traces(kind, label, seed):
    result = detector.classify(traces.generate(kind, seed))
    assert result.label == label
    if label == "possible_fall":
        assert 0.5 < result.score < 1.0
    else:
        assert result.score == 0.0


def test_trends_flag_decline_without_diagnosis():
    trends = {t.metric: t for t in compute_trends(simulated.generate("parent_001"))}
    steps = trends["steps"]
    assert steps.direction == "down" and steps.notable
    assert steps.percent_change is not None and steps.percent_change < -25
    for t in trends.values():
        lowered = t.observation.lower()
        assert not any(w in lowered for w in ("disease", "diagnos", "depress", "illness"))


def test_trends_insufficient_data():
    trends = compute_trends(simulated.generate("parent_001", days=6))
    assert all(t.direction == "insufficient_data" and not t.notable for t in trends)
