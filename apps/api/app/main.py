import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.db import init_db, session_scope
from app.routes import demo, ingest, parents, system, webhooks
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
    version="0.1.0",
    description="Care coordination for aging parents and their distant families. "
    "Hackathon prototype — not a medical device.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "Last-Event-ID"],
)
app.include_router(system.router)
app.include_router(parents.router)
app.include_router(ingest.router)
app.include_router(webhooks.router)
app.include_router(demo.router)
