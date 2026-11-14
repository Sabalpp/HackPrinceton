"""Offline checks that our Photon SDK usage builds valid, contract-checked requests."""

from types import SimpleNamespace

from app.config import get_settings
from app.integrations.photon import client as photon

SPACE = "pho_spc_0" + "a" * 25
AGENT = "pho_usr_0" + "b" * 25


class FakeProjects:
    def __init__(self):
        self.sent = None
        self.listed = None

    def send_message(self, inp):
        self.sent = inp
        return SimpleNamespace(model_dump=lambda **_: {"id": "pho_msg_0" + "c" * 25})

    def list_messages(self, inp):
        self.listed = inp
        return SimpleNamespace(items=[], nextPageToken=None)


def test_send_text_builds_sdk_request(monkeypatch):
    fake = SimpleNamespace(projects=FakeProjects())
    monkeypatch.setattr(photon, "get_client", lambda: fake)
    monkeypatch.setattr(get_settings(), "photon_project_id", "pho_prj_test")
    mid = photon.send_text(space_id=SPACE, sender_id=AGENT, text="hi", idempotency_key="k:1")
    assert mid.startswith("pho_msg_")
    sent = fake.projects.sent
    assert sent.path.id == "pho_prj_test"
    assert sent.headers.idempotency_key == "k:1"
    body = sent.body.model_dump(mode="json", exclude_none=True, by_alias=True)
    assert body["spaceId"] == SPACE and body["senderId"] == AGENT
    assert body["content"] == {"type": "text", "format": "plain", "text": "hi"}


def test_list_received_builds_sdk_query(monkeypatch):
    from datetime import UTC, datetime

    fake = SimpleNamespace(projects=FakeProjects())
    monkeypatch.setattr(photon, "get_client", lambda: fake)
    monkeypatch.setattr(get_settings(), "photon_project_id", "pho_prj_test")
    assert photon.list_received_since(datetime(2026, 11, 14, 15, 30, tzinfo=UTC)) == []
    query = fake.projects.listed.query.model_dump(mode="json", exclude_none=True, by_alias=True)
    assert query["direction"] == "received" and query["createdSince"].startswith("2026-11-14T15:30")
