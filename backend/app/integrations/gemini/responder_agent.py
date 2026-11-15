"""FarCare for helpers (trusted neighbors / family friends), by text or in the app.

Plain YES / NO / DONE about the current task is handled deterministically before this runs
(see services/conversation.py). This agent answers everything else ("what do I need to do?",
"I can't make the 4pm one", "on my way, 10 minutes") with tools that call the same actions
as the helper's app screen. Helpers only ever see their own assigned tasks: the parent's
first name, the task, the time and task details. Never health information.
"""

import inspect
import logging
import re
from typing import Any

from sqlmodel import Session

from app.config import get_settings
from app.integrations.gemini.agent import _decl, _get_client, history
from app.models import CareRequestRow, Person
from app.services import actions, care_requests

log = logging.getLogger(__name__)

PROMPT = """You are FarCare, texting with {name}, a trusted helper (neighbor or family friend) \
in an older adult's care circle. Be brief, warm and practical: 1-2 short sentences.

Tools:
- list_my_tasks: the tasks assigned to {name} right now (only their own).
- respond_to_task: accept (with optional minutes until arrival), decline, or mark complete.
  Use the task_id from list_my_tasks. If several tasks could match, ask which one.

Never invent tasks, times or people. Never discuss anyone's health. If {name} reports an \
emergency, tell them to call local emergency services first."""

LIST_RE = re.compile(
    r"\b(task|tasks|what do i|anything|need me|to ?do|status|any requests)\b", re.I
)
ETA_RE = re.compile(r"\b(\d{1,3})\s*(?:min|mins|minutes)\b", re.I)


def _task_view(session: Session, row: CareRequestRow) -> dict:
    parent = session.get(Person, row.parent_id)
    return {
        "task_id": row.id,
        "for": parent.display_name.split(" ")[0] if parent else "someone",
        "what": care_requests.label(row),
        "details": row.details or "",
        "status": row.status,
        "emergency": row.origin == "emergency",
    }


class ResponderTools:
    def __init__(self, session: Session, responder: Person):
        self.s, self.responder = session, responder
        self.actions: list[str] = []

    def run(self, name: str, args: dict) -> dict:
        self.actions.append(name)
        fn = getattr(self, f"t_{name}", None)
        if fn is None:
            return {"error": f"unknown tool {name}"}
        allowed = inspect.signature(fn).parameters
        try:
            return fn(**{k: v for k, v in args.items() if k in allowed})
        except actions.ActionError as exc:
            return {"error": str(exc)}
        except TypeError as exc:
            return {"error": f"bad arguments: {exc}"}

    def t_list_my_tasks(self) -> dict:
        rows = actions.responder_tasks(self.s, self.responder, open_only=True)
        return {"tasks": [_task_view(self.s, r) for r in rows]}

    def t_respond_to_task(
        self, task_id: str, decision: str, eta_minutes: int | None = None
    ) -> dict:
        if decision not in ("accept", "decline", "complete"):
            return {"error": "decision must be accept, decline or complete"}
        eta = int(eta_minutes) if eta_minutes else None
        row = actions.respond_to_task(self.s, self.responder, task_id, decision, eta)
        return {"done": True, "task": _task_view(self.s, row)}


def _declarations() -> list[Any]:
    return [
        _decl("list_my_tasks", "Tasks assigned to this helper right now.", {}, []),
        _decl(
            "respond_to_task",
            "Accept, decline or complete one assigned task.",
            {
                "task_id": {"type": "string"},
                "decision": {"type": "string", "enum": ["accept", "decline", "complete"]},
                "eta_minutes": {"type": "integer", "description": "Minutes until arrival"},
            },
            ["task_id", "decision"],
        ),
    ]


def _app_link(channel: str) -> str:
    url = get_settings().public_app_url.rstrip("/")
    return (
        f" You can also see your tasks in the app: {url}/respond"
        if url and channel != "app"
        else ""
    )


def respond(
    session: Session, responder: Person, text: str, channel: str = "text"
) -> tuple[str, str]:
    """Returns (reply, ai_mode)."""
    tools = ResponderTools(session, responder)
    if get_settings().gemini_enabled:
        try:
            reply = _gemini(session, responder, text, tools)
            if reply:
                return reply, "gemini"
        except Exception as exc:
            log.warning(
                "Gemini unavailable for helper chat, using fallback: %s", type(exc).__name__
            )
    return _fallback(responder, text, tools, channel), "fallback"


def _gemini(session: Session, responder: Person, text: str, tools: ResponderTools) -> str:
    from google.genai import types

    contents: list[Any] = [
        types.Content(
            role="user" if t.role == "user" else "model", parts=[types.Part.from_text(text=t.text)]
        )
        for t in history(session, responder.id)
    ]
    if not contents or contents[-1].role != "user":
        contents.append(types.Content(role="user", parts=[types.Part.from_text(text=text)]))
    config = types.GenerateContentConfig(
        system_instruction=PROMPT.format(name=responder.display_name),
        tools=[types.Tool(function_declarations=_declarations())],
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )
    for _ in range(4):
        resp = _get_client().models.generate_content(
            model=get_settings().gemini_model, contents=contents, config=config
        )
        calls = resp.function_calls or []
        if not calls:
            return (resp.text or "").strip()
        contents.append(resp.candidates[0].content)
        contents.append(
            types.Content(
                role="user",
                parts=[
                    types.Part.from_function_response(
                        name=c.name or "",
                        response={"result": tools.run(c.name or "", dict(c.args or {}))},
                    )
                    for c in calls
                ],
            )
        )
    return ""


def _fallback(responder: Person, text: str, tools: ResponderTools, channel: str) -> str:
    tasks = tools.run("list_my_tasks", {})["tasks"]
    if not tasks:
        return f"Thanks, {responder.display_name}! Nothing needs your help right now."
    if LIST_RE.search(text) or len(tasks) > 1:
        lines = [
            f"- {t['what']} for {t['for']} ({t['status'].replace('_', ' ')})" for t in tasks[:5]
        ]
        hint = (
            "Reply YES or NO for a new request, or DONE when you've finished."
            if len(tasks) == 1
            else "Tell me which one you mean, or use the app to reply."
        )
        return "Your tasks:\n" + "\n".join(lines) + f"\n{hint}" + _app_link(channel)
    task = tasks[0]
    eta = ETA_RE.search(text)
    if eta and task["status"] == "sent":
        tools.run(
            "respond_to_task",
            {"task_id": task["task_id"], "decision": "accept", "eta_minutes": int(eta.group(1))},
        )
        return ""  # apply_responder_reply already confirmed with the helper
    hint = (
        "Reply YES if you can help, or NO if you can't."
        if task["status"] == "sent"
        else "Reply DONE when you've finished, or NEED HELP if more help is needed."
    )
    return f"Sorry, I didn't catch that. {hint}"
