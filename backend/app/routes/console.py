"""Demo Console: a single page to watch FarCare work (phones + family dashboard + controls).

Served only when DEMO_MODE=true. Every action goes through the admin-token demo API. The
default dev token is pre-filled only outside production; otherwise the page asks for it.
"""

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from app.config import get_settings

router = APIRouter(include_in_schema=False)
PAGE = Path(__file__).resolve().parents[1] / "static" / "console.html"


@router.get("/console", response_class=HTMLResponse)
def console() -> HTMLResponse:
    settings = get_settings()
    if not settings.demo_mode:
        raise HTTPException(404, "Not found")
    dev_token = settings.demo_admin_token
    prefill = dev_token if settings.app_env != "production" and dev_token.startswith("dev-") else ""
    html = PAGE.read_text(encoding="utf-8").replace("__DEV_TOKEN__", json.dumps(prefill))
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})
