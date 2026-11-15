"""FarCare for family members (e.g. the distant son). Same rules as the parent agent:
the backend executes tools for the authenticated sender, and a deterministic fallback keeps
working without Gemini. Family can ask, but the PARENT decides: help requests always go to
the parent for consent first.
"""

import inspect
import logging
import re
from typing import Any

from sqlmodel import Session

from app.config import get_settings
from app.integrations.gemini.agent import (
    CATEGORY_KEYWORDS,
    REQUESTABLE,
    WHEN_RE,
    _decl,
    _get_client,
)
from app.models import FamilyLink, Person
from app.schemas.contracts import AssistanceCategory
from app.services import family

log = logging.getLogger(__name__)

PROMPT = """You are FarCare, texting with {name}, who lives far away from {who} \
({parent_name}). Be brief and warm: 1-3 short sentences, like a text message.

Tools:
- get_parent_status: what {parent_name} has chosen to share (open requests, alerts, latest \
check-in, activity trend). Only state facts from this tool.
- request_help_for_parent: arrange practical help from {parent_name}'s Care Circle. FarCare \
always asks {parent_name} for consent first, so tell {name} you're checking with {who} first. \
Never say help is arranged until it is.
- check_on_parent: when {name} is worried or can't reach {who}. FarCare checks in with \
{parent_name} and alerts the Care Circle automatically if there's no answer.

Never diagnose or give medical advice. If {name} describes an emergency, tell them to call \
local emergency services for {who}'s location, and use check_on_parent."""

CONCERN_RE = re.compile(
    r"not (answering|responding|picking up|replying)|haven'?t heard|no (reply|answer|response)"
    r"|can'?t reach|worried|check on (her|him|them|mom|dad|mum|ama|baba)|is (she|he) (ok|okay)",
    re.I,
)
STATUS_RE = re.compile(r"^\s*(status|update|how is|how's|any news)", re.I)


class FamilyTools:
    def __init__(self, session: Session, member: Person, link: FamilyLink, parent: Person):
        self.s, self.member, self.link, self.parent = session, member, link, parent
        self.actions: list[str] = []

    def run(self, name: str, args: dict) -> dict:
        self.actions.append(name)
        fn = getattr(self, f"t_{name}", None)
        if fn is None:
            return {"error": f"unknown tool {name}"}
        allowed = inspect.signature(fn).parameters
        try:
            return fn(**{k: v for k, v in args.items() if k in allowed})
        except TypeError as exc:
            return {"error": f"bad arguments: {exc}"}

    def t_get_parent_status(self) -> dict:
        return {"facts": family.status_lines(self.s, self.link, self.parent)}

    def t_request_help_for_parent(self, category: str, when: str = "", details: str = "") -> dict:
        cat = AssistanceCategory(category) if category in REQUESTABLE else AssistanceCategory.other
        return family.request_help(self.s, self.member, self.link, self.parent, cat, when, details)

    def t_check_on_parent(self, reason: str = "") -> dict:
        return family.check_on_parent(self.s, self.member, self.link, self.parent, reason)


def _declarations() -> list[Any]:
    return [
        _decl("get_parent_status", "What the parent has chosen to share right now.", {}, []),
        _decl(
            "request_help_for_parent",
            "Ask the parent's Care Circle for practical help (parent must consent first).",
            {
                "category": {"type": "string", "enum": REQUESTABLE},
                "when": {"type": "string"},
                "details": {"type": "string", "description": "Task details only."},
            },
            ["category"],
        ),
        _decl(
            "check_on_parent",
            "Send the parent a safety check-in; escalates if no answer.",
            {"reason": {"type": "string"}},
            [],
        ),
    ]


def respond(
    session: Session, member: Person, link: FamilyLink, parent: Person, text: str
) -> tuple[str, str]:
    """Returns (reply, ai_mode)."""
    tools = FamilyTools(session, member, link, parent)
    if get_settings().gemini_enabled:
        try:
            return _gemini(session, member, link, parent, text, tools), "gemini"
        except Exception as exc:
            log.warning(
                "Gemini unavailable for family chat, using fallback: %s", type(exc).__name__
            )
    return _fallback(member, link, parent, text, tools), "fallback"


def _gemini(
    session: Session,
    member: Person,
    link: FamilyLink,
    parent: Person,
    text: str,
    tools: FamilyTools,
) -> str:
    from google.genai import types

    # Stored turns have no parent/grant version. They may contain facts from a
    # revoked or deleted link, even when another current link grants access.
    # Use this request plus freshly permission-filtered tools until history has
    # consent-scoped storage. This cannot erase data sent before revocation.
    contents: list[Any] = [types.Content(role="user", parts=[types.Part.from_text(text=text)])]
    config = types.GenerateContentConfig(
        system_instruction=PROMPT.format(
            name=member.display_name, who=family.who(link, parent), parent_name=parent.display_name
        ),
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


def _fallback(
    member: Person, link: FamilyLink, parent: Person, text: str, tools: FamilyTools
) -> str:
    name = family.who(link, parent)
    lower = text.lower()
    if CONCERN_RE.search(text):
        result = tools.run("check_on_parent", {"reason": text[:200]})
        if result.get("status") == "already_active":
            return f"I'm already checking on {name}. I'll update you here."
        return (
            f"I've sent {name} a check-in now. If {name} doesn't answer or asks for help, "
            "I'll alert the Care Circle and tell you right away."
        )
    if not STATUS_RE.search(text):
        for category, words in CATEGORY_KEYWORDS:
            if any(w in lower for w in words):
                when = WHEN_RE.search(text)
                result = tools.run(
                    "request_help_for_parent",
                    {"category": category.value, "when": when.group(0) if when else ""},
                )
                status = result.get("status")
                if status == "asked_parent_for_consent":
                    return (
                        f"I've asked {name} first. If {name} says yes, I'll ask "
                        f"{result.get('proposed_helper')} and keep you posted."
                    )
                return f"I couldn't set that up: {result.get('note', 'please try again')}"
    facts = tools.run("get_parent_status", {})["facts"]
    return "\n".join(facts)
