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


def test_set_typing_builds_sdk_request(monkeypatch):
    calls = []
    fake = SimpleNamespace(projects=SimpleNamespace(set_space_typing=calls.append))
    monkeypatch.setattr(photon, "get_client", lambda: fake)
    monkeypatch.setattr(get_settings(), "photon_project_id", "pho_prj_test")
    photon.set_typing(space_id=SPACE, sender_id=AGENT)
    [inp] = calls
    assert inp.path.model_dump(by_alias=True) == {"id": "pho_prj_test", "spaceId": SPACE}
    assert inp.body.model_dump(by_alias=True) == {"senderId": AGENT, "typing": True}


def test_show_typing_never_raises(monkeypatch):
    from app.integrations import messaging
    from app.models import Person

    monkeypatch.setattr(messaging, "channel_name", lambda: "photon")

    def boom(**_):
        raise RuntimeError("photon down")

    monkeypatch.setattr(photon, "set_typing", boom)
    person = Person(
        id="p", role="parent", display_name="P", photon_space_id=SPACE, photon_agent_user_id=AGENT
    )
    messaging.show_typing(person)  # must not raise


# --------------------------------------------------------------------------- setup script


def _load_setup_script():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "photon_setup.py"
    spec = importlib.util.spec_from_file_location("photon_setup", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeAdmin:
    def __init__(self, existing_handles=()):
        self.created = []
        self.webhooks = []
        existing = [
            SimpleNamespace(model_dump=lambda h=h, **_: {"handle": h, "releasedAt": None})
            for h in existing_handles
        ]
        assignments = SimpleNamespace(
            list=lambda inp: SimpleNamespace(assignments=existing),
            create=self._create,
        )
        self.projects = SimpleNamespace(
            platforms=SimpleNamespace(imessage=SimpleNamespace(assignments=assignments)),
            list_webhook_api_versions=lambda inp: SimpleNamespace(
                model_dump=lambda **_: {"apiVersions": ["2026-07-01"]}
            ),
            create_webhook_destination=self._webhook,
        )

    def _create(self, inp):
        self.created.append(inp)
        return SimpleNamespace(model_dump=lambda **_: {"assignedPhoneNumber": "+15550109999"})

    def _webhook(self, inp):
        self.webhooks.append(inp)
        return SimpleNamespace(model_dump=lambda **_: {"signingSecret": "whsec_x"})


def test_setup_assign_skips_placeholders_and_existing(monkeypatch):
    setup = _load_setup_script()
    fake = FakeAdmin(existing_handles=["+447700900123"])
    monkeypatch.setattr(setup, "get_client", lambda: fake)
    s = get_settings()
    monkeypatch.setattr(s, "demo_parent_handle", "+447700900123")  # already assigned
    monkeypatch.setattr(s, "demo_child_handle", "aarav@example.com")  # new
    setup.assign("pho_prj_test")
    [inp] = fake.created
    assert inp.body.model_dump(exclude_none=True) == {"handle": "aarav@example.com"}
    assert inp.headers.idempotency_key.startswith("farcare-assign-child-")


def test_setup_webhook_subscribes_to_message_received(monkeypatch):
    setup = _load_setup_script()
    fake = FakeAdmin()
    monkeypatch.setattr(setup, "get_client", lambda: fake)
    setup.webhook("pho_prj_test", "https://api.example.com/webhooks/photon")
    [inp] = fake.webhooks
    body = inp.body.model_dump(by_alias=True, exclude_none=True)
    assert (
        body["enabledEvents"]
        == ["message.received", "message.dispatched", "message.delivered", "message.failed"]
        and body["apiVersion"] == "2026-07-01"
    )
    assert body["url"] == "https://api.example.com/webhooks/photon"


def test_setup_sms_status_handles_transport_error_without_leaking_details(monkeypatch, capsys):
    from photon_api.errors import TransportError

    setup = _load_setup_script()
    settings = get_settings()
    monkeypatch.setattr(settings, "photon_api_key", "offline-test-key")
    monkeypatch.setattr(settings, "photon_project_id", "pho_prj_test")
    monkeypatch.setattr(setup.sys, "argv", ["photon_setup.py", "sms-status"])

    def unavailable(_):
        raise TransportError(
            "DNS failed for https://private.example.test/path?token=private-token; "
            "Authorization: Bearer private-token; body=private-body",
            operation_id="private-operation",
            request_id="private-request",
        )

    monkeypatch.setattr(
        setup,
        "get_client",
        lambda: SimpleNamespace(projects=SimpleNamespace(list_users=unavailable)),
    )
    assert setup.main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "Photon network request failed. Check DNS/connectivity and retry; "
        "SMS eligibility was not verified.\n"
    )
    assert "private" not in captured.err and "Traceback" not in captured.err
