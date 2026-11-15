import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.db import init_db, session_scope
from app.routes import (
    accounts,
    chat,
    console,
    demo,
    experience,
    ingest,
    parents,
    personal_health,
    system,
    webhooks,
)
from app.seed import seed
from app.services.background import poll_loop, tick_loop

log = logging.getLogger("farcare")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    init_db()
    if settings.seed_demo_data:
        with session_scope() as s:
            seed(s)
    tasks: list[asyncio.Task] = []
    if settings.background_tasks_enabled:
        tasks.append(asyncio.create_task(tick_loop()))
        if settings.use_photon and settings.photon_delivery == "poll":
            tasks.append(asyncio.create_task(poll_loop()))
    log.info(
        "FarCare API ready (messaging=%s, ai=%s, demo=%s)",
        "photon" if settings.use_photon else "console",
        "gemini" if settings.gemini_enabled else "fallback",
        settings.demo_mode,
    )
    yield
    for task in tasks:
        task.cancel()


app = FastAPI(
    title="FarCare API",
    version="0.2.0",
    description="Care coordination for aging parents and their distant families. "
    "Invite-first pilot with role-specific accounts, personal learning and a durable messaging worker. "
    "Not a diagnostic service or emergency response service.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "Last-Event-ID", "X-CSRF-Token"],
)


@app.exception_handler(RequestValidationError)
async def validation_error(_request: Request, exc: RequestValidationError):
    # Default validation bodies echo inputs, including passwords and invitation tokens.
    return JSONResponse(
        status_code=422,
        content={
            "detail": [{k: error[k] for k in ("loc", "msg", "type")} for error in exc.errors()]
        },
        headers={"Cache-Control": "no-store"},
    )


@app.middleware("http")
async def privacy_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "private, no-store"
    return response


app.include_router(system.router)
app.include_router(accounts.router)
app.include_router(experience.router)
app.include_router(chat.router)
app.include_router(personal_health.router)
app.include_router(parents.router)
app.include_router(ingest.router)
app.include_router(webhooks.router)
app.include_router(demo.router)
app.include_router(console.router)
