"""Photon webhook verification/dedupe and the Gemini tool loop (with a fake client)."""

import base64
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from standardwebhooks.webhooks import Webhook

from app.config import get_settings
from app.db import session_scope
from app.models import InboundMessageRow, Person
from tests.conftest import CHILD

SECRET = "whsec_" + base64.b64encode(b"farcare-test-signing-secret-1234").decode()


def photon_event(message_id: str, handle: str, text: str) -> dict:
    return {
        "id": f"evt_{message_id}",
        "type": "message.received",
        "apiVersion": "2026-07-01",
        "source": "photon",
        "timestamp": "2026-11-14T15:30:00Z",
        "data": {
            "projectId": "pho_prj_test",
            "organizationId": "org",
            "object": {
                "id": message_id,
                "status": "received",
                "platform": "imessage",
                "sentBy": {"id": "pho_usr_human", "kind": "human", "handle": handle},
                "receivedBy": {"id": "pho_usr_agent", "kind": "agent", "handle": "+15550109999"},
                "space": {"id": "pho_spc_direct", "kind": "direct"},
                "content": {"type": "text", "format": "plain", "text": text},
            },
        },
    }


def signed(body: bytes, webhook_id: str) -> dict:
    ts = datetime.now(UTC)
    sig = Webhook(SECRET).sign(webhook_id, ts, body.decode())
    return {
        "webhook-id": webhook_id,
        "webhook-timestamp": str(int(ts.timestamp())),
        "webhook-signature": sig,
        "content-type": "application/json",
    }


@pytest.fixture
def webhook_ready(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "photon_webhook_secret", SECRET)
    monkeypatch.setattr(get_settings(), "photon_project_id", "pho_prj_test")
    return client


def test_webhook_rejects_bad_signature(webhook_ready):
    body = json.dumps(photon_event("pho_msg_1", "+15550100003", "hi")).encode()
    headers = signed(body, "evt_1") | {"webhook-signature": "v1,AAAA"}
    assert webhook_ready.post("/webhooks/photon", content=body, headers=headers).status_code == 400


def test_webhook_processes_once_and_learns_routing(webhook_ready):
    body = json.dumps(photon_event("pho_msg_2", "+15550100003", "JOIN")).encode()
    for _ in range(2):  # Photon redelivery
        r = webhook_ready.post("/webhooks/photon", content=body, headers=signed(body, "evt_2"))
        assert r.status_code == 204
    with session_scope() as s:
        assert s.query(InboundMessageRow).count() == 1
        assert s.get(InboundMessageRow, "pho_msg_2").status == "done"
        asha = s.get(Person, "responder_001")
        assert asha.photon_space_id == "pho_spc_direct" and asha.reachable_since is not None


def test_webhook_ignores_other_projects_and_event_types(webhook_ready):
    ev = photon_event("pho_msg_3", "+15550100003", "hi")
    ev["data"]["projectId"] = "pho_prj_someone_else"
    body = json.dumps(ev).encode()
    assert (
        webhook_ready.post(
            "/webhooks/photon", content=body, headers=signed(body, "evt_3")
        ).status_code
        == 204
    )
    body = json.dumps({"id": "evt_4", "type": "message.delivered", "data": {}}).encode()
    assert (
        webhook_ready.post(
            "/webhooks/photon", content=body, headers=signed(body, "evt_4")
        ).status_code
        == 204
    )
    with session_scope() as s:
        assert s.query(InboundMessageRow).count() == 0


# --------------------------------------------------------------------------- Gemini loop


class FakeGemini:
    """Returns a function call first, then a text answer. Records what it was sent."""

    def __init__(self, call_name: str, call_args: dict, final_text: str):
        from google.genai import types

        self.calls = []
        self._responses = [
            SimpleNamespace(
                function_calls=[SimpleNamespace(name=call_name, args=call_args)],
                candidates=[
                    SimpleNamespace(
                        content=types.Content(
                            role="model",
                            parts=[
                                types.Part(
                                    function_call=types.FunctionCall(name=call_name, args=call_args)
                                )
                            ],
                        )
                    )
                ],
                text=None,
            ),
            SimpleNamespace(function_calls=None, candidates=[], text=final_text),
        ]
        self.models = SimpleNamespace(generate_content=self._generate)

    def _generate(self, *, model, contents, config):
        self.calls.append(contents)
        return self._responses.pop(0)


def test_gemini_tool_call_is_executed_for_authenticated_parent(client, monkeypatch):
    from app.integrations.gemini import agent

    fake = FakeGemini(
        "request_assistance",
        {
            "category": "grocery_assistance",
            "when": "tomorrow",
            "details": "rice and lentils",
            "parent_id": "parent_999",
        },
        "Of course! I can help with that.",
    )
    monkeypatch.setattr(get_settings(), "gemini_api_key", "test-key")
    monkeypatch.setattr(agent, "_get_client", lambda: fake)
    from tests.conftest import say, texts_to

    out = say(client, "parent_001", "Can someone pick up rice and lentils for me tomorrow?")
    [reply] = texts_to(out, "parent")
    assert reply.startswith("Of course!") and "Reply YES or NO" in reply
    reqs = client.get("/api/parents/parent_001/care-requests", headers=CHILD).json()
    assert reqs[0]["details"] == "rice and lentils" and reqs[0]["parent_id"] == "parent_001"
    assert len(fake.calls) == 2  # model saw the tool result before answering


def test_gemini_failure_falls_back(client, monkeypatch):
    from app.integrations.gemini import agent

    def broken():
        raise RuntimeError("quota exhausted")

    monkeypatch.setattr(get_settings(), "gemini_api_key", "test-key")
    monkeypatch.setattr(agent, "_get_client", broken)
    from tests.conftest import say, texts_to

    out = say(client, "parent_001", "hello")
    assert texts_to(out, "parent") == ["Hi Sita! 😊 How are you feeling today?"]


def test_model_question_is_dropped_and_redundant_details_skipped(client, monkeypatch):
    """Live Gemini asked its own 'Shall I send that request?' on top of ours (seen in a real
    run) and passed details='groceries'. Parent sees one question; Asha sees no echo."""
    from app.integrations.gemini import agent
    from tests.conftest import say, texts_to

    fake = FakeGemini(
        "request_assistance",
        {"category": "grocery_assistance", "when": "tomorrow", "details": "groceries"},
        "I can ask Asha to help you with groceries tomorrow. Shall I send that request?",
    )
    monkeypatch.setattr(get_settings(), "gemini_api_key", "test-key")
    monkeypatch.setattr(agent, "_get_client", lambda: fake)
    out = say(client, "parent_001", "Can someone help me with groceries tomorrow?")
    [reply] = texts_to(out, "parent")
    assert reply.count("?") == 1 and "Shall I" not in reply
    assert reply.startswith("I can ask Asha to help you with groceries tomorrow.")
    out = say(client, "parent_001", "yes")
    [ask] = texts_to(out, "responder")
    assert "Details:" not in ask
