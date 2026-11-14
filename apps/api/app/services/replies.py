"""Deterministic reply parsing.

Safety-critical decisions (is the parent OK? did they ask for help? did the responder
accept?) are parsed here with plain rules first. The LLM is never allowed to downgrade
an emergency; it is only consulted for ambiguous, non-safety text.

Includes a few romanized Nepali words for the demo persona (ho/hajur = yes,
hoina = no, thik chu = I'm fine, maddat/bachau = help).
"""

import re
from typing import Literal

YES_WORDS = {
    "yes",
    "y",
    "yeah",
    "yep",
    "yup",
    "sure",
    "ok",
    "okay",
    "please",
    "please do",
    "go ahead",
    "sounds good",
    "ho",
    "hajur",
    "huncha",
    "hunchha",
    "absolutely",
    "of course",
}
NO_WORDS = {
    "no",
    "n",
    "nope",
    "nah",
    "don't",
    "dont",
    "do not",
    "cancel",
    "stop",
    "hoina",
    "not now",
    "never mind",
    "nevermind",
}

OK_PATTERNS = [
    r"\bi'?m (ok|okay|fine|alright|all right|good|safe)\b",
    r"\b(i am|im) (ok|okay|fine|alright|all right|good|safe)\b",
    r"^(ok|okay|fine|safe|all good|im good|i'm good|thik chu|thik cha|ma thik chu)\b",
    r"\bfalse alarm\b",
    r"\bno(t)? (fall|fallen)\b",
    r"\bdropped (my|the) (phone|watch)\b",
]
HELP_PATTERNS = [
    r"^help\b",
    r"\bneed help\b",
    r"\bhelp me\b",
    r"\bsend (someone|help)\b",
    r"\bmaddat\b",
    r"\bbachau\b",
    r"\bnot (ok|okay|fine)\b",
    r"\bi'?m hurt\b",
    r"\bhurt\b",
]
# Phrases that always open/escalate an incident, regardless of what the LLM thinks.
# Kept narrow on purpose: "can you help me with groceries" must NOT match. Broader or
# indirect phrasing is handled by the agent's report_urgent_help tool (LLM may escalate,
# never suppress).
EMERGENCY_PATTERNS = [
    r"\bi (have )?(fell|fallen)\b",
    r"\bi'?ve fallen\b",
    r"\bjust fell\b",
    r"\bcan'?t get up\b",
    r"\bcannot get up\b",
    r"\bchest pain\b",
    r"\bcan'?t breathe\b",
    r"\bcannot breathe\b",
    r"\btrouble breathing\b",
    r"\bcall (an )?ambulance\b",
    r"\bbleeding\b",
    r"\bunconscious\b",
    r"\bbachau\b",
]


def _norm(text: str) -> str:
    text = text.lower().replace("’", "'")
    return re.sub(r"\s+", " ", re.sub(r"[!.?,;:]+", " ", text)).strip()


def _first_words(text: str, n: int = 3) -> list[str]:
    words = _norm(text).split(" ")
    return [" ".join(words[:i]) for i in range(1, min(n, len(words)) + 1)]


def parse_yes_no(text: str) -> Literal["yes", "no"] | None:
    norm = _norm(text)
    if norm.startswith("no problem") or norm.startswith("no worries"):
        return "yes"
    heads = _first_words(text)
    if any(h in NO_WORDS for h in heads):
        return "no"
    if any(h in YES_WORDS for h in heads):
        return "yes"
    return None


def parse_safety_reply(text: str) -> Literal["ok", "help"] | None:
    """Reply to 'Are you okay?'. HELP wins over OK when both appear (fail safe)."""
    norm = _norm(text)
    if any(re.search(p, norm) for p in HELP_PATTERNS) or looks_like_emergency(text):
        return "help"
    if any(re.search(p, norm) for p in OK_PATTERNS):
        return "ok"
    return None


def looks_like_emergency(text: str) -> bool:
    norm = _norm(text)
    return any(re.search(p, norm) for p in EMERGENCY_PATTERNS)


ResponderIntent = Literal["accepted", "declined", "completed", "need_more_help"]

_DONE = [
    r"^done\b",
    r"\bdone\b",
    r"\bcompleted?\b",
    r"\bfinished\b",
    r"\ball set\b",
    r"\bdropped (them|it|the groceries) off\b",
    r"\b(she|he)'?s (ok|okay|fine|safe)\b",
    r"\bi'?m (here|with (her|him))\b",
    r"\bhelped\b",
]
_MORE = [
    r"\bneed(s)? (more|an?) (help|ambulance|doctor)\b",
    r"\bambulance\b",
    r"\bhospital\b",
    r"\bnot breathing\b",
    r"\bunconscious\b",
    r"\bcall 911\b",
    r"\bserious\b",
    r"\binjured\b",
]
_ACCEPT = [
    r"\bon my way\b",
    r"\bomw\b",
    r"\bi can (help|come|do|go)\b",
    r"\bcoming\b",
    r"\bwill do\b",
    r"\bheading (over|there)\b",
    r"\bi'?ll (go|come|be there|help)\b",
]
_DECLINE = [
    r"\bcan'?t\b",
    r"\bcannot\b",
    r"\bunable\b",
    r"\bbusy\b",
    r"\bnot available\b",
    r"\bunavailable\b",
    r"\bsorry\b",
    r"\bout of town\b",
]


def parse_responder_reply(text: str, current_status: str) -> ResponderIntent | None:
    norm = _norm(text)
    if any(re.search(p, norm) for p in _MORE):
        return "need_more_help"
    if current_status == "accepted" and any(re.search(p, norm) for p in _DONE):
        return "completed"
    yn = parse_yes_no(text)
    if yn == "no" or (yn is None and any(re.search(p, norm) for p in _DECLINE)):
        return "declined" if current_status == "sent" else None
    if yn == "yes" or any(re.search(p, norm) for p in _ACCEPT):
        return "accepted" if current_status == "sent" else None
    return None


def parse_eta(text: str) -> tuple[int | None, str | None]:
    """Returns (minutes, clock_time_text). e.g. 'yes 15 min' -> (15, None); 'yes at 4pm' -> (None, '4 PM')."""
    norm = _norm(text)
    minutes = None
    m = re.search(r"\b(\d{1,3})\s*(?:min|mins|minute|minutes|m)\b", norm)
    if m:
        minutes = int(m.group(1))
    else:
        h = re.search(r"\b(?:an?|one) hour\b|\b(\d)\s*(?:hr|hrs|hour|hours)\b", norm)
        if h:
            minutes = 60 * int(h.group(1) or 1)
    clock = None
    c = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", norm)
    if c:
        clock = f"{int(c.group(1))}{':' + c.group(2) if c.group(2) else ''} {c.group(3).upper()}"
    return minutes, clock
