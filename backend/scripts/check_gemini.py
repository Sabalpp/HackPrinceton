"""Check which Gemini models work with your key, including a real tool call. Reads backend/.env.

    uv run python scripts/check_gemini.py                  # GEMINI_MODEL + common candidates
    uv run python scripts/check_gemini.py gemini-3.8-flash # specific models

Each candidate costs about one request of quota. Prints the first model that completes the
FarCare tool-calling round trip, which is what you should set as GEMINI_MODEL.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from google.genai import types  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.integrations.gemini.agent import _declarations, _get_client  # noqa: E402

CANDIDATES = [
    "gemini-3.5-flash-lite",
    "gemini-3.8-flash",
    "gemini-3.1-flash-lite",
    "gemini-flash-latest",
]
PROMPT = "Can someone help me with groceries tomorrow afternoon? I need rice and lentils."


def try_model(model: str) -> bool:
    config = types.GenerateContentConfig(
        system_instruction="You coordinate help for an older adult. Use tools when appropriate.",
        tools=[types.Tool(function_declarations=_declarations())],
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )
    started = time.monotonic()
    try:
        resp = _get_client().models.generate_content(model=model, contents=PROMPT, config=config)
    except Exception as exc:  # report 404 / 429 / auth errors without stack traces
        print(f"  {model:<24} FAIL {type(exc).__name__}: {str(exc)[:160]}")
        return False
    calls = resp.function_calls or []
    took = time.monotonic() - started
    if calls and calls[0].name == "request_assistance":
        print(
            f"  {model:<24} OK   tool call {calls[0].name}({dict(calls[0].args or {})}) "
            f"in {took:.1f}s"
        )
        return True
    print(f"  {model:<24} WEAK no request_assistance call (text: {(resp.text or '')[:80]!r})")
    return False


def main() -> int:
    settings = get_settings()
    if not settings.gemini_enabled:
        raise SystemExit("Set GEMINI_API_KEY in backend/.env first.")
    models = sys.argv[1:] or list(dict.fromkeys([settings.gemini_model, *CANDIDATES]))
    print("Testing tool calling:")
    for model in models:
        if try_model(model):
            print(f"\nUse: GEMINI_MODEL={model}")
            return 0
    print("\nNo model completed the round trip. FarCare will use its deterministic fallback.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
