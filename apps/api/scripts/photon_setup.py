"""Photon 2.0 (Beta) project setup for the FarCare demo. Reads apps/api/.env.

    uv run python scripts/photon_setup.py status
    uv run python scripts/photon_setup.py sms-status        # read-only sender/campaign check
    uv run python scripts/photon_setup.py assign            # shared line for each DEMO_*_HANDLE
    uv run python scripts/photon_setup.py webhook --url https://<api-host>/webhooks/photon

Needs PHOTON_API_KEY (pho_sk_..., scopes project:read, project:write, platforms:write) and
PHOTON_PROJECT_ID (pho_prj_...). Assignments use plan capacity. After assigning, EVERY cast
member must text their assigned number once before FarCare can message them.
Docs: https://photon.codes/docs/beta/get-started/imessage
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from photon_api import ApiError  # noqa: E402
from photon_api.errors import TransportError  # noqa: E402
from photon_api.generated.models import (  # noqa: E402
    CreateSharedLineAssignmentRequest,
    CreateWebhookDestinationRequest,
)
from photon_api.rpc_generated import (  # noqa: E402
    CreateSharedLineAssignmentHeader,
    CreateSharedLineAssignmentInput,
    CreateSharedLineAssignmentPath,
    CreateWebhookDestinationHeader,
    CreateWebhookDestinationInput,
    CreateWebhookDestinationPath,
    GetSmsLineCampaignAssignmentInput,
    ListSharedLineAssignmentsInput,
    ListUsersInput,
    ListWebhookApiVersionsInput,
    ListWebhookDestinationsInput,
)

from app.config import get_settings  # noqa: E402
from app.integrations.photon.client import get_client  # noqa: E402
from app.integrations.photon.inbound import normalize_handle  # noqa: E402

ROLES = {
    "parent": "demo_parent_handle",
    "child": "demo_child_handle",
    "responder": "demo_responder_handle",
    "responder2": "demo_responder2_handle",
}
PLACEHOLDER_PREFIX = "+1555"  # reserved fictional numbers from .env.example


def _dump(obj) -> dict:
    return obj.model_dump(mode="json", by_alias=True) if obj is not None else {}


def assignments(project: str) -> list[dict]:
    page = get_client().projects.platforms.imessage.assignments.list(
        ListSharedLineAssignmentsInput.model_validate({"path": {"id": project}})
    )
    return [_dump(a) for a in (page.assignments or [])]


def status(project: str) -> None:
    settings = get_settings()
    if settings.photon_platform == "sms":
        sms_status(project)
        return
    by_handle = {
        normalize_handle(a.get("handle") or a.get("email") or ""): a
        for a in assignments(project)
        if not a.get("releasedAt")
    }
    print("Shared iMessage line assignments:")
    for role, attr in ROLES.items():
        handle = normalize_handle(getattr(settings, attr))
        a = by_handle.get(handle)
        line = a["assignedPhoneNumber"] if a else "NOT ASSIGNED"
        print(f"  {role:<11} {_mask(handle):<16} -> {line}")
    dests = get_client().projects.list_webhook_destinations(
        ListWebhookDestinationsInput.model_validate({"path": {"projectId": project}})
    )
    print("Webhook destinations:")
    for d in _dump(dests).get("destinations", []):
        print(f"  {d.get('status')} {d.get('url')} events={d.get('enabledEvents')}")


def assign(project: str) -> None:
    settings = get_settings()
    if settings.photon_platform != "imessage":
        raise SystemExit("assign is only for shared iMessage lines. Use sms-status for SMS.")
    existing = {
        normalize_handle(a.get("handle") or "")
        for a in assignments(project)
        if not a.get("releasedAt")
    }
    for role, attr in ROLES.items():
        handle = normalize_handle(getattr(settings, attr))
        if not handle or handle.startswith(PLACEHOLDER_PREFIX):
            print(f"  {role}: skipped (set {attr.upper()} in apps/api/.env)")
            continue
        if handle in existing:
            print(f"  {role}: already assigned")
            continue
        result = get_client().projects.platforms.imessage.assignments.create(
            CreateSharedLineAssignmentInput(
                path=CreateSharedLineAssignmentPath(id=project),
                headers=CreateSharedLineAssignmentHeader.model_validate(
                    {"Idempotency-Key": f"farcare-assign-{role}-{handle}"[:255]}
                ),
                body=CreateSharedLineAssignmentRequest(handle=handle),
            )
        )
        print(
            f"  {role}: {_mask(handle)} -> text FarCare at {_dump(result)['assignedPhoneNumber']}"
        )
    print("Next: each person texts their number once (e.g. 'JOIN').")


def sms_status(project: str) -> None:
    """Read existing SMS sender identities and eligibility; never buy/register a number."""
    settings = get_settings()
    query = {"platform": "sms", "pageSize": 100}
    found = False
    while True:
        page = _dump(
            get_client().projects.list_users(
                ListUsersInput.model_validate({"path": {"id": project}, "query": query})
            )
        )
        for user in page.get("items", []):
            if user.get("kind") != "agent" or user.get("platform") != "sms":
                continue
            found = True
            selected = " [configured]" if user.get("id") == settings.photon_agent_user_id else ""
            print(f"SMS agent {user['id']}{selected} ({_mask(user.get('handle', ''))})")
            resource = (user.get("details") or {}).get("resourceId")
            if resource:
                state = _dump(
                    get_client().projects.get_sms_line_campaign_assignment(
                        GetSmsLineCampaignAssignmentInput.model_validate(
                            {"path": {"id": project, "resourceId": resource}}
                        )
                    )
                )
                print(f"  Campaign eligibility: {state.get('eligibility', 'unknown')}")
            else:
                print("  No SMS line resource in this user; verify the sender in Photon dashboard.")
        token = page.get("nextPageToken")
        if not token:
            break
        query["pageToken"] = token
    if not found:
        print("No SMS agent found. Set up an eligible SMS sender in the Photon dashboard first.")
    print("Set PHOTON_PLATFORM=sms and PHOTON_AGENT_USER_ID to the selected sender ID.")
    print(
        "Recipients must authorize the sender, then text JOIN from their registered FarCare number."
    )
    print("Campaign eligibility is not recipient consent or proof of delivery.")


def webhook(project: str, url: str) -> None:
    if not url.startswith("https://") or not url.endswith("/webhooks/photon"):
        raise SystemExit("URL must be https://<public-host>/webhooks/photon")
    versions = (
        _dump(
            get_client().projects.list_webhook_api_versions(
                ListWebhookApiVersionsInput.model_validate({"path": {"projectId": project}})
            )
        ).get("apiVersions")
        or []
    )
    version = (
        "2026-07-01" if "2026-07-01" in versions else (versions[-1] if versions else "2026-07-01")
    )
    resp = get_client().projects.create_webhook_destination(
        CreateWebhookDestinationInput(
            path=CreateWebhookDestinationPath.model_validate({"projectId": project}),
            headers=CreateWebhookDestinationHeader.model_validate(
                {"Idempotency-Key": f"farcare-webhook-{url}"[:255]}
            ),
            body=CreateWebhookDestinationRequest(
                name="FarCare API",
                url=url,
                apiVersion=version,
                enabledEvents=[
                    "message.received",
                    "message.dispatched",
                    "message.delivered",
                    "message.failed",
                ],
            ),
        )
    )
    secret = _dump(resp).get("signingSecret")
    print(f"Destination created (apiVersion {version}).")
    print("Put this in the backend env as PHOTON_WEBHOOK_SECRET (shown once, keep it private):")
    print(secret)


def _mask(handle: str) -> str:
    return handle if "@" in handle or len(handle) < 6 else handle[:3] + "…" + handle[-2:]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("assign")
    sub.add_parser("sms-status")
    wh = sub.add_parser("webhook")
    wh.add_argument("--url", required=True)
    args = ap.parse_args()

    settings = get_settings()
    if not settings.photon_enabled:
        raise SystemExit("Set PHOTON_API_KEY and PHOTON_PROJECT_ID in apps/api/.env first.")
    project = settings.photon_project_id
    try:
        if args.cmd == "status":
            status(project)
        elif args.cmd == "assign":
            assign(project)
        elif args.cmd == "sms-status":
            sms_status(project)
        else:
            webhook(project, args.url)
    except TransportError:
        print(
            "Photon network request failed. Check DNS/connectivity and retry; "
            "SMS eligibility was not verified.",
            file=sys.stderr,
        )
        return 1
    except ApiError as err:
        print(
            f"Photon API error {err.status} (request {err.request_id or 'unknown'})",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
