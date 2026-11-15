"""End-to-end Care Circle coordination (console channel, fallback AI)."""

from tests.conftest import CHILD, say, texts_to


def latest_request(client):
    r = client.get("/api/parents/parent_001/care-requests", headers=CHILD)
    assert r.status_code == 200
    return r.json()[0]


def test_grocery_request_full_lifecycle(client):
    out = say(client, "parent_001", "Can someone help me with groceries tomorrow?")
    assert any("Asha" in t and "YES or NO" in t for t in texts_to(out, "parent"))
    req = latest_request(client)
    assert req["status"] == "awaiting_approval"
    assert req["request_type"] == "grocery_assistance"
    assert req["responder_id"] == "responder_001" and req["parent_approved"] is False
    assert texts_to(out, "responder") == []  # nobody contacted before approval

    out = say(client, "parent_001", "Yes")
    req = latest_request(client)
    assert req["status"] == "sent" and req["parent_approved"] is True
    [ask] = texts_to(out, "responder")
    assert "groceries" in ask and "Sita" in ask
    assert "tired" not in ask.lower()  # no health info leaks to responders

    out = say(client, "responder_001", "Yes, I can help at 4 PM")
    req = latest_request(client)
    assert req["status"] == "accepted" and req["responder_note"] == "4 PM"
    assert any("Asha" in t and "accepted" in t for t in texts_to(out, "child"))
    assert any("Good news" in t for t in texts_to(out, "parent"))

    say(client, "responder_001", "Done, dropped the groceries off")
    assert latest_request(client)["status"] == "completed"

    kinds = [
        e["kind"] for e in client.get("/api/parents/parent_001/timeline", headers=CHILD).json()
    ]
    assert "care_request.created" in kinds and "care_request.updated" in kinds


def test_decline_proposes_next_authorized_responder(client):
    say(client, "parent_001", "I need help with groceries")
    say(client, "parent_001", "yes")
    out = say(client, "responder_001", "Sorry, I can't today")
    assert any("Ram" in t and "YES or NO" in t for t in texts_to(out, "parent"))
    req = latest_request(client)
    assert req["status"] == "awaiting_approval" and req["responder_id"] == "responder_002"
    out = say(client, "parent_001", "yes")
    assert latest_request(client)["status"] == "sent"
    assert texts_to(out, "responder")


def test_parent_can_decline(client):
    say(client, "parent_001", "Could someone give me a ride to my appointment?")
    req = latest_request(client)
    assert req["responder_id"] == "responder_002"  # only Ram is authorized for rides
    out = say(client, "parent_001", "No")
    assert latest_request(client)["status"] == "cancelled"
    assert texts_to(out, "responder") == []


def test_unknown_responder_reply_and_unknown_sender(client):
    out = say(client, "responder_001", "hello?")
    assert any("Nothing needs your help" in t for t in texts_to(out, "responder"))


def test_wellness_checkin_recorded_and_shared_with_consent(client):
    out = say(client, "parent_001", "I haven't been feeling very energetic lately.")
    assert any("sleeping" in t for t in texts_to(out, "parent"))
    overview = client.get("/api/parents/parent_001/overview", headers=CHILD).json()
    assert overview["latest_checkin"]["energy"] == "low"
    assert overview["health"]["suggest_checkin"] is True
    assert overview["health"]["data_mode"] == "simulated"
