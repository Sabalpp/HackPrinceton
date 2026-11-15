"""The app and the AI messaging agent are one system: same conversation, same actions."""

from app.config import get_settings
from app.db import session_scope
from app.models import CareRequestRow, FamilyLink
from tests.conftest import ADMIN
from tests.test_experience import ORIGIN, become, demo, get, post
from tests.test_webhook_and_agent import FakeGemini


def chat(client, actor, text):
    r = post(client, actor, "/api/me/conversation", {"text": text})
    assert r.status_code == 200, r.text
    return r.json()["messages"]


def replies(messages):
    return [m["text"] for m in messages if m["direction"] == "out"]


def test_parent_requests_help_in_app_and_helper_handles_it_in_app(client):
    parent, helper = demo(client, "parent"), demo(client, "responder")

    thread = chat(client, parent, "Can someone help me with groceries tomorrow?")
    assert thread[0]["direction"] == "in" and thread[0]["channel"] == "app"
    assert "YES or NO" in replies(thread)[-1]
    assert all(m["channel"] == "in_app" for m in thread if m["direction"] == "out")

    chat(client, parent, "yes")
    helper_thread = get(client, helper, "/api/me/conversation").json()["messages"]
    assert any("groceries" in t for t in replies(helper_thread))

    # The helper asks a question in plain words, then accepts with an ETA.
    listed = chat(client, helper, "what do I need to do?")
    assert "Your tasks" in replies(listed)[-1] and "groceries" in replies(listed)[-1]
    chat(client, helper, "YES 10")
    with session_scope() as s:
        assert s.query(CareRequestRow).one().status == "accepted"
    parent_thread = get(client, parent, "/api/me/conversation").json()["messages"]
    assert any(t.startswith("Good news") for t in replies(parent_thread))


def test_text_and_app_are_the_same_conversation(client):
    """A text sent by iMessage shows up in the app thread, and the app continues it."""
    parent = demo(client, "parent")
    client.cookies.clear()  # the admin demo endpoint must not run as the signed-in parent
    client.post(
        "/api/demo/inbound",
        headers=ADMIN,
        json={"person_id": "parent_001", "text": "I need help with groceries"},
    )
    thread = get(client, parent, "/api/me/conversation").json()["messages"]
    assert [m["direction"] for m in thread][:2] == ["in", "out"]
    assert "YES or NO" in thread[1]["text"]
    chat(client, parent, "yes")  # approve from the app what was asked by text
    with session_scope() as s:
        assert s.query(CareRequestRow).one().status == "sent"


def test_parent_cancels_by_text_and_helper_is_told_first(client):
    parent, helper = demo(client, "parent"), demo(client, "responder")
    chat(client, parent, "Can someone help me with groceries tomorrow?")
    chat(client, parent, "yes")
    out = chat(client, parent, "please cancel the groceries, I don't need it anymore")
    assert "cancelled" in replies(out)[-1]
    with session_scope() as s:
        assert s.query(CareRequestRow).one().status == "cancelled"
    helper_thread = get(client, helper, "/api/me/conversation").json()["messages"]
    assert any("no longer needs" in t for t in replies(helper_thread))


def test_parent_status_by_text(client):
    parent = demo(client, "parent")
    chat(client, parent, "Can someone help me with groceries tomorrow?")
    out = chat(client, parent, "any updates?")
    assert "help with groceries" in replies(out)[-1]


def test_gemini_changes_sharing_through_the_same_action_as_the_app(client, monkeypatch):
    from app.integrations.gemini import agent

    parent = demo(client, "parent")
    fake = FakeGemini(
        "update_sharing",
        {"family_member": "Aarav", "share_health": False},
        "Done. Aarav will no longer see your health data.",
    )
    monkeypatch.setattr(get_settings(), "gemini_api_key", "test-key")
    monkeypatch.setattr(agent, "_get_client", lambda: fake)
    out = chat(client, parent, "Please stop sharing my health data with Aarav")
    assert "no longer see your health" in replies(out)[-1]
    with session_scope() as s:
        link = s.query(FamilyLink).one()
        assert link.share_health is False and link.share_checkins is True  # untouched


def test_conversation_is_private_and_protected(client, monkeypatch):
    parent, child = demo(client, "parent"), demo(client, "child")
    chat(client, parent, "I've been feeling tired lately")
    child_thread = get(client, child, "/api/me/conversation").json()["messages"]
    assert not any("tired" in m["text"] for m in child_thread)

    become(client, parent)
    no_csrf = client.post("/api/me/conversation", json={"text": "hi"}, headers={"Origin": ORIGIN})
    assert no_csrf.status_code == 403
    client.cookies.clear()
    assert client.get("/api/me/conversation").status_code == 401

    from app.routes import chat as chat_route

    monkeypatch.setattr(chat_route, "APP_MESSAGES_PER_10_MIN", 1)
    assert post(client, parent, "/api/me/conversation", {"text": "hi"}).status_code == 429


def test_texts_link_back_to_the_app_when_configured(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "public_app_url", "https://farcare.example")
    r = client.post(
        "/api/demo/inbound",
        headers=ADMIN,
        json={"person_id": "parent_001", "text": "I need help with groceries"},
    )
    [reply] = [m["text"] for m in r.json()["outbound"] if m["to_role"] == "parent"]
    assert "https://farcare.example/parent" in reply
    parent = demo(client, "parent")
    out = chat(client, parent, "can someone pick up my medicine?")
    assert "farcare.example" not in replies(out)[-1]  # no link inside the app itself


def test_care_request_during_open_safety_alert_is_not_an_emergency_answer(client):
    """Found live: an old ESCALATED alert made 'help me with groceries' read as HELP and
    replied 'Help is already on the way'. While the alert is still waiting for 'Are you
    okay?', FarCare keeps insisting on that answer first (safe default)."""
    from datetime import timedelta

    from app.db import utcnow
    from app.models import SafetyIncidentRow
    from app.services.background import run_tick
    from tests.test_safety_flow import fall_event

    client.post("/api/events/fall", json=fall_event())
    parent = demo(client, "parent")
    waiting = chat(client, parent, "Can someone help me with groceries tomorrow?")
    assert "Are you okay?" in replies(waiting)[-1]

    with session_scope() as s:  # no answer in time -> escalated, like the stale live alert
        s.query(SafetyIncidentRow).one().check_deadline = utcnow() - timedelta(seconds=1)
    run_tick()
    out = chat(client, parent, "Can someone help me with groceries tomorrow?")
    assert "YES or NO" in replies(out)[-1]
    assert not any("Help is already on the way" in t for t in replies(out)[-1:])
    # Real distress during the escalated alert is still treated as HELP.
    out = chat(client, parent, "help me, I fell")
    assert "Help is already on the way" in replies(out)[-1]


def test_safety_prompts_reply_in_app_when_parent_chats_in_app(client):
    from tests.test_safety_flow import fall_event

    client.post("/api/events/fall", json=fall_event())
    parent = demo(client, "parent")
    out = chat(client, parent, "what?")  # ambiguous during a safety check
    last = [m for m in out if m["direction"] == "out"][-1]
    assert "reply OK" in last["text"] and last["channel"] == "in_app"


def test_model_claim_of_contact_is_removed_before_approval(client, monkeypatch):
    from app.integrations.gemini import agent

    fake = FakeGemini(
        "request_assistance",
        {"category": "grocery_assistance", "when": "tomorrow"},
        "I've asked Asha if she can help you with groceries tomorrow.",
    )
    monkeypatch.setattr(get_settings(), "gemini_api_key", "test-key")
    monkeypatch.setattr(agent, "_get_client", lambda: fake)
    parent = demo(client, "parent")
    [reply] = replies(chat(client, parent, "groceries tomorrow please"))[-1:]
    assert "I've asked" not in reply and reply.startswith("Would you like me to ask Asha")
