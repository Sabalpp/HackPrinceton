"""FarCare wellness companion (Gemini function calling) + deterministic fallback.

Design rules:
- Gemini handles language understanding and conversation. The BACKEND executes every tool
  and always uses the authenticated sender as the parent — tool args can't pick a parent.
- Gemini may escalate (report_urgent_help) but can never suppress or downgrade a safety
  workflow; those run before the agent is called (see services/conversation.py).
- If Gemini is unconfigured, out of quota, or errors, a keyword-based fallback produces the
  same tool calls so the demo still works. Every reply records which mode produced it.
"""

import inspect
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlmodel import Session, select

from app.config import get_settings
from app.db import new_id, utcnow
from app.health import service as health
from app.models import CareCircleMember, CareRequestRow, CheckInRow, ConversationTurn, Person
from app.schemas.contracts import AssistanceCategory, CareRequestStatus
from app.services import actions, care_requests, safety, timeline

log = logging.getLogger(__name__)

REQUESTABLE = [c.value for c in AssistanceCategory if c != AssistanceCategory.urgent_check_in]

SYSTEM_PROMPT = """You are FarCare, a warm, patient companion who texts with {name}, an older \
adult who lives far away from their family. Their local time is {local_time}.

Style: short, kind, plain language (1-3 short sentences, like a text message). Reply in the \
same language {name} writes in. Ask at most one gentle follow-up question.

What you can do (via tools):
- request_assistance: when {name} wants practical help (groceries, a ride, medicine pickup, \
a meal, household help, a visit). The system picks an authorized person from their Care \
Circle and asks {name} to approve BEFORE anyone is contacted. Do not ask for approval \
yourself and never claim someone was contacted; the system adds the approval question.
- record_checkin: when {name} shares how they feel (energy, sleep, mood, pain). Summarize \
neutrally in their words.
- get_activity_summary: recent activity compared with their own usual pattern.
- list_care_circle / get_request_status: who can help, and status of existing requests.
- get_my_status: what is happening right now (open requests, any safety check, latest \
check-in), the same facts {name}'s FarCare app shows.
- cancel_request: when {name} no longer needs an open request. If they have several, ask \
which one. Anyone already asked is told it is no longer needed.
- update_sharing: when {name} clearly asks to start or stop sharing their health data or \
check-ins with a named family member. Confirm what changed in your reply.
- report_urgent_help: if they describe a fall, injury, chest pain, trouble breathing, or \
anything that could be an emergency. When unsure, use it.

Turns starting with "(by voice call)" were spoken on a phone call. If the latest message is \
spoken, reply in plain, speakable sentences without emoji or lists. In later text messages you \
may refer back to what was said on the call.

Hard rules: never diagnose, never give medical instructions, never predict medical events. \
For health worries, suggest talking to their doctor. Do not invent facts about people or \
requests; only use tool results. If request_assistance returns no_helper_available, say \
plainly that nobody in their Care Circle can help with that right now and that their family \
has been told. Never imply that help is arranged or on the way unless a tool result says so.

Context (non-diagnostic): {context}"""


def _decl(name: str, description: str, properties: dict, required: list[str]) -> Any:
    from google.genai import types

    return types.FunctionDeclaration(
        name=name,
        description=description,
        parameters_json_schema={"type": "object", "properties": properties, "required": required},
    )


def _declarations() -> list[Any]:
    return [
        _decl(
            "get_my_status",
            "What is happening for them right now (requests, safety, check-in).",
            {},
            [],
        ),
        _decl(
            "cancel_request",
            "Cancel one of their open help requests.",
            {"what": {"type": "string", "description": "Which request, e.g. 'groceries'"}},
            [],
        ),
        _decl(
            "update_sharing",
            "Start or stop sharing health data / check-ins with family.",
            {
                "family_member": {"type": "string", "description": "Name or relation, e.g. Aarav"},
                "share_health": {"type": "boolean"},
                "share_checkins": {"type": "boolean"},
            },
            ["family_member"],
        ),
        _decl(
            "request_assistance",
            "Start a request for practical help from the Care Circle.",
            {
                "category": {"type": "string", "enum": REQUESTABLE},
                "when": {
                    "type": "string",
                    "description": "When, in their words (e.g. 'tomorrow 4pm')",
                },
                "details": {
                    "type": "string",
                    "description": "Task details only (items, destination). No health info.",
                },
            },
            ["category"],
        ),
        _decl(
            "record_checkin",
            "Save a short wellness check-in from what they shared.",
            {
                "summary": {"type": "string"},
                "mood": {"type": "string", "enum": ["good", "okay", "low", "unknown"]},
                "energy": {"type": "string", "enum": ["high", "normal", "low", "unknown"]},
            },
            ["summary"],
        ),
        _decl(
            "get_activity_summary",
            "Recent activity vs. their usual pattern (non-diagnostic).",
            {},
            [],
        ),
        _decl("list_care_circle", "List the trusted people who can help and with what.", {}, []),
        _decl("get_request_status", "Status of their open help requests.", {}, []),
        _decl(
            "report_urgent_help",
            "Escalate a possible emergency to family and Care Circle.",
            {"description": {"type": "string"}},
            ["description"],
        ),
    ]


@dataclass
class AgentResult:
    reply: str
    mode: Literal["gemini", "fallback"]
    actions: list[str] = field(default_factory=list)
    request: CareRequestRow | None = None
    escalated: bool = False


class Tools:
    """Executes tool calls for ONE authenticated parent."""

    def __init__(self, session: Session, parent: Person):
        self.s = session
        self.parent = parent
        self.actions: list[str] = []
        self.request: CareRequestRow | None = None
        self.escalated = False

    def run(self, name: str, args: dict) -> dict:
        self.actions.append(name)
        try:
            fn = getattr(self, f"t_{name}", None)
            if fn is None:
                return {"error": f"unknown tool {name}"}
            # Drop anything not in the tool's signature (e.g. a model-invented parent_id):
            # the acting parent is always the authenticated sender, never a tool argument.
            allowed = inspect.signature(fn).parameters
            return fn(**{k: v for k, v in args.items() if k in allowed})
        except TypeError as exc:
            return {"error": f"bad arguments: {exc}"}

    def t_request_assistance(self, category: str, when: str = "", details: str = "") -> dict:
        if category not in REQUESTABLE:
            category = AssistanceCategory.other.value
        pending = care_requests.pending_approval(self.s, self.parent.id)
        if pending is not None:
            return {
                "status": "already_waiting_for_approval",
                "note": "Ask them to answer the pending YES/NO question first.",
            }
        req = care_requests.create_request(
            self.s,
            self.parent,
            AssistanceCategory(category),
            details=details,
            requested_time=when or None,
        )
        self.request = req
        if req.status == CareRequestStatus.awaiting_approval:
            responder = self.s.get(Person, req.responder_id)
            return {
                "status": "awaiting_parent_approval",
                "proposed_helper": responder.display_name if responder else None,
            }
        return {
            "status": "no_helper_available",
            "note": "Nobody in the Care Circle can help with this right now; family was told.",
        }

    def t_record_checkin(
        self, summary: str, mood: str = "unknown", energy: str = "unknown"
    ) -> dict:
        mood = mood if mood in ("good", "okay", "low", "unknown") else "unknown"
        energy = energy if energy in ("high", "normal", "low", "unknown") else "unknown"
        row = CheckInRow(
            id=new_id("chk"),
            parent_id=self.parent.id,
            summary=summary[:300],
            mood=mood,
            energy=energy,
        )
        self.s.add(row)
        self.s.flush()
        timeline.record(
            self.s,
            self.parent.id,
            "checkin.recorded",
            f"Check-in: {row.summary}",
            ref_id=row.id,
            data={"mood": mood, "energy": energy},
            visibility=timeline.CHECKIN,
        )
        return {"saved": True}

    def t_get_activity_summary(self) -> dict:
        summ = health.summary(self.s, self.parent.id)
        return {
            "data_mode": summ.data_mode,
            "observations": [t.observation for t in summ.trends],
            "note": "Describe gently. Not a diagnosis.",
        }

    def t_list_care_circle(self) -> dict:
        rows = self.s.exec(
            select(CareCircleMember, Person).where(
                CareCircleMember.parent_id == self.parent.id,
                CareCircleMember.active == True,  # noqa: E712
                CareCircleMember.responder_id == Person.id,
            )
        ).all()
        return {
            "people": [
                {
                    "name": p.display_name,
                    "relationship": m.relationship,
                    "can_help_with": m.categories,
                }
                for m, p in rows
            ]
        }

    def t_get_request_status(self) -> dict:
        reqs = care_requests.open_requests(self.s, self.parent.id)
        out = []
        for r in reqs:
            helper = self.s.get(Person, r.responder_id) if r.responder_id else None
            out.append(
                {
                    "what": care_requests.label(r),
                    "status": r.status,
                    "helper": helper.display_name if helper else None,
                }
            )
        return {"open_requests": out}

    def t_get_my_status(self) -> dict:
        return actions.parent_status(self.s, self.parent)

    def t_cancel_request(self, what: str = "") -> dict:
        open_reqs = [
            r
            for r in care_requests.open_requests(self.s, self.parent.id)
            if r.origin == "conversation"
        ]
        wanted = what.strip().lower()
        if wanted:
            open_reqs = [
                r
                for r in open_reqs
                if wanted in care_requests.label(r).lower()
                or wanted.replace(" ", "_") in r.request_type
            ] or open_reqs
        if not open_reqs:
            return {"status": "nothing_to_cancel"}
        if len(open_reqs) > 1:
            return {
                "status": "ask_which",
                "open_requests": [care_requests.label(r) for r in open_reqs],
            }
        try:
            row = actions.cancel_request(self.s, self.parent, open_reqs[0].id)
        except actions.ActionError as exc:
            return {"error": str(exc)}
        return {"status": "cancelled", "what": care_requests.label(row)}

    def t_update_sharing(
        self,
        family_member: str = "",
        share_health: bool | None = None,
        share_checkins: bool | None = None,
    ) -> dict:
        link = actions.family_by_name(self.s, self.parent, family_member)
        if link is None:
            return {"status": "ask_who"}
        health = link.share_health if share_health is None else bool(share_health)
        checkins = link.share_checkins if share_checkins is None else bool(share_checkins)
        actions.set_sharing(self.s, self.parent, link.member_id, health, checkins)
        member = self.s.get(Person, link.member_id)
        return {
            "status": "updated",
            "member": member.display_name if member else family_member,
            "share_health": health,
            "share_checkins": checkins,
        }

    def t_report_urgent_help(self, description: str = "") -> dict:
        safety.open_from_message(self.s, self.parent, description or "(urgent help requested)")
        self.escalated = True
        return {"escalated": True, "note": "Family and Care Circle are being alerted."}


# --------------------------------------------------------------------------- entry point


def respond(session: Session, parent: Person, text: str, channel: str = "text") -> AgentResult:
    tools = Tools(session, parent)
    mode: Literal["gemini", "fallback"] = "fallback"
    reply = ""
    if get_settings().gemini_enabled:
        try:
            reply = _gemini_reply(session, parent, text, tools)
            mode = "gemini"
        except Exception as exc:
            log.warning("Gemini unavailable, using fallback: %s", type(exc).__name__)
    if mode == "fallback":
        reply = _fallback_reply(session, parent, text, tools)

    if tools.escalated:
        reply = ""  # safety workflow already messaged the parent
    elif tools.request is not None and tools.request.status == CareRequestStatus.awaiting_approval:
        prompt = care_requests.approval_prompt(session, tools.request)
        # The approval question is ours, asked exactly once. Models sometimes ask their own
        # ("Shall I send that request?"), so keep only the model's non-question sentences.
        lead = _without_questions(reply)
        reply = f"{lead}\n\n{prompt}" if lead else prompt
        url = get_settings().public_app_url.rstrip("/")
        if url and channel != "app":
            reply += f"\n\nYou can also follow it in the FarCare app: {url}/parent"
    return AgentResult(
        reply=reply,
        mode=mode,
        actions=tools.actions,
        request=tools.request,
        escalated=tools.escalated,
    )


CLAIMS_CONTACT = re.compile(
    r"\b(i'?ve|i have|i(?:'| ha)ve already|already)\s+(asked|contacted|messaged|texted|sent|"
    r"reached out|notified|told)\b|\b(is|are) on (?:the|her|his|their) way\b|\bon the way\b",
    re.I,
)


def _without_questions(text: str) -> str:
    """Keep the model's statements, minus its own questions and any claim that someone was
    already contacted: before the parent's YES, nobody has been."""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    return " ".join(
        x for x in sentences if x and not x.endswith("?") and not CLAIMS_CONTACT.search(x)
    ).strip()


def history(session: Session, person_id: str, limit: int = 12) -> list[ConversationTurn]:
    rows = session.exec(
        select(ConversationTurn)
        .where(ConversationTurn.person_id == person_id)
        .order_by(ConversationTurn.id.desc())
        .limit(limit)  # type: ignore[union-attr]
    ).all()
    return list(reversed(rows))


def _context(session: Session, parent: Person) -> str:
    summ = health.summary(session, parent.id)
    notable = [t.observation for t in summ.trends if t.notable]
    open_reqs = [
        care_requests.label(r) + f" ({r.status})"
        for r in care_requests.open_requests(session, parent.id)
    ]
    parts = []
    if notable:
        parts.append(
            "Activity vs usual: "
            + " ".join(notable)
            + (" (simulated data)" if summ.data_mode == "simulated" else "")
        )
    if open_reqs:
        parts.append("Open help requests: " + "; ".join(open_reqs))
    return " ".join(parts) or "none"


_client = None


def _get_client():
    global _client
    if _client is None:
        from google import genai
        from google.genai import types

        settings = get_settings()
        _client = genai.Client(
            api_key=settings.gemini_api_key,
            http_options=types.HttpOptions(timeout=int(settings.gemini_timeout_seconds * 1000)),
        )
    return _client


def _gemini_reply(session: Session, parent: Person, text: str, tools: Tools) -> str:
    from zoneinfo import ZoneInfo

    from google.genai import types

    settings = get_settings()
    local = utcnow().astimezone(ZoneInfo(parent.timezone)).strftime("%A %-I:%M %p")
    contents: list[Any] = []
    # history() already contains the current user turn (saved by the router before calling).
    for turn in history(session, parent.id):
        role = "user" if turn.role == "user" else "model"
        contents.append(types.Content(role=role, parts=[types.Part.from_text(text=turn.text)]))
    if not contents or contents[-1].role != "user":
        contents.append(types.Content(role="user", parts=[types.Part.from_text(text=text)]))
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT.format(
            name=parent.display_name, local_time=local, context=_context(session, parent)
        ),
        tools=[types.Tool(function_declarations=_declarations())],
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )
    client = _get_client()
    for _ in range(4):
        resp = client.models.generate_content(
            model=settings.gemini_model, contents=contents, config=config
        )
        calls = resp.function_calls or []
        if not calls:
            return (resp.text or "").strip()
        contents.append(resp.candidates[0].content)  # keeps thought signatures intact
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


# --------------------------------------------------------------------------- fallback

CATEGORY_KEYWORDS: list[tuple[AssistanceCategory, list[str]]] = [
    (
        AssistanceCategory.grocery_assistance,
        ["grocer", "shopping", "vegetable", "market", "milk", "rice", "food shop"],
    ),
    (
        AssistanceCategory.medication_pickup,
        ["medicine", "medication", "pharmacy", "prescription", "pills", "aushadi"],
    ),
    (
        AssistanceCategory.transportation,
        ["ride", "drive me", "lift", "taxi", "appointment", "take me to"],
    ),
    (AssistanceCategory.meal_assistance, ["cook", "meal", "dinner", "lunch"]),
    (AssistanceCategory.household_help, ["fix", "repair", "clean", "heavy", "light bulb", "leak"]),
    (
        AssistanceCategory.companionship,
        ["lonely", "company", "visit me", "come over", "someone to talk"],
    ),
]
WHEN_RE = re.compile(
    r"\b(today|tonight|tomorrow(?: morning| afternoon| evening)?|this (?:morning|afternoon|"
    r"evening|weekend)|(?:on |next )?(?:monday|tuesday|wednesday|thursday|friday|saturday|"
    r"sunday)|at \d{1,2}(?::\d{2})?\s*(?:am|pm)?)\b",
    re.I,
)
WELLNESS = [
    "tired",
    "energy",
    "energetic",
    "sleep",
    "slept",
    "pain",
    "ache",
    "dizzy",
    "sad",
    "weak",
    "feeling",
    "feel",
    "thakai",
    "lonely",
]
LOW_WORDS = ["tired", "not", "haven't", "low", "sad", "weak", "pain", "dizzy", "lonely", "bad"]
GREETINGS = ("hi", "hello", "hey", "namaste", "good morning", "good evening")


def _fallback_reply(session: Session, parent: Person, text: str, tools: Tools) -> str:
    lower = text.lower()
    name = parent.display_name
    if re.search(r"\b(cancel|don'?t need|no longer need|never ?mind)\b", lower):
        what = next((w for c, ws in CATEGORY_KEYWORDS for w in ws if w in lower), "")
        result = tools.run("cancel_request", {"what": what})
        if result.get("status") == "cancelled":
            return f"Okay, I've cancelled {result['what']}. Anyone I'd asked has been told."
        if result.get("status") == "ask_which":
            return "Which one should I cancel? " + "; ".join(result["open_requests"])
        return "You don't have any open requests to cancel."
    if re.search(r"\b(status|any updates?|what'?s happening|what is happening)\b", lower):
        st = tools.run("get_my_status", {})
        parts = [
            f"{r['what']}: {r['status']}" + (f" with {r['helper']}" if r["helper"] else "")
            for r in st.get("open_requests", [])
        ]
        return (
            ("Here's where things are: " + "; ".join(parts) + ".")
            if parts
            else (f"Nothing is pending right now, {name}. How are you feeling today?")
        )
    for category, words in CATEGORY_KEYWORDS:
        if any(w in lower for w in words):
            when = WHEN_RE.search(text)
            result = tools.run(
                "request_assistance",
                {"category": category.value, "when": when.group(0) if when else "", "details": ""},
            )
            if result.get("status") == "awaiting_parent_approval":
                return "Of course, I can help arrange that."
            if result.get("status") == "already_waiting_for_approval":
                return "Let's finish your earlier request first."
            return (
                "I'm sorry, no one in your Care Circle can help with that right now. "
                "I've let your family know."
            )
    if any(w in lower for w in WELLNESS):
        low = any(w in lower for w in LOW_WORDS)
        tired = any(w in lower for w in ("tired", "energy", "energetic", "weak", "thakai"))
        tools.run(
            "record_checkin",
            {
                "summary": text[:200],
                "mood": "low" if low else "okay",
                "energy": "low" if tired else "unknown",
            },
        )
        if tired:
            return (
                f"I'm sorry you've been feeling tired, {name}. Have you been sleeping "
                "differently than usual?"
            )
        return f"Thank you for telling me, {name}. How has your sleep been lately?"
    if lower.strip().startswith(GREETINGS):
        return f"Hi {name}! 😊 How are you feeling today?"
    return (
        f"I'm here for you, {name}. You can tell me how you're feeling, or ask for help "
        "with things like groceries, a ride, or medicine."
    )


# --------------------------------------------------------------------------- responder parsing


def classify_responder_reply(text: str, current_status: str) -> str | None:
    """Gemini fallback for ambiguous responder replies. Returns an intent or None."""
    if not get_settings().gemini_enabled:
        return None
    from google.genai import types

    allowed = (
        ["accepted", "declined", "need_more_help", "unclear"]
        if current_status == "sent"
        else ["completed", "need_more_help", "unclear"]
    )
    try:
        resp = _get_client().models.generate_content(
            model=get_settings().gemini_model,
            contents=f"A volunteer was asked to help an older neighbor. Their reply: {text!r}. "
            f"Classify it.",
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_json_schema={
                    "type": "object",
                    "properties": {"intent": {"type": "string", "enum": allowed}},
                    "required": ["intent"],
                },
            ),
        )
        import json

        intent = json.loads(resp.text or "{}").get("intent")
        return intent if intent in allowed and intent != "unclear" else None
    except Exception as exc:
        log.warning("Gemini classify failed: %s", type(exc).__name__)
        return None
