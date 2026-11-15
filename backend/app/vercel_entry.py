"""Vercel Services entrypoint for the existing authenticated Tiger-backed API."""

from app.vercel_runtime import configure

configure()

from fastapi import FastAPI  # noqa: E402

from app.main import app as core  # noqa: E402

# Vercel Services preserves the original /backend path. Mounted ASGI routing
# handles that prefix and redirects, while reusing the existing app lifecycle.
app = FastAPI(
    docs_url=None, redoc_url=None, openapi_url=None, lifespan=core.router.lifespan_context
)
app.mount("/backend", core)


@app.middleware("http")
async def private_responses(request, call_next):
    response = await call_next(request)
    # The core middleware matches /api; the public URL now includes /backend.
    response.headers["Cache-Control"] = "private, no-store"
    return response
