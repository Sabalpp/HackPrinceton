# FarCare API image; production runs app.worker in a separate supervised container.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.15 /uv /uvx /bin/

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app
COPY scripts ./scripts

RUN useradd --create-home --uid 10001 farcare
USER farcare

ENV PATH="/app/.venv/bin:$PATH" APP_ENV=production
EXPOSE 8000
# --timeout-graceful-shutdown: live dashboard streams (SSE) never close on their own, so
# without it a restart/redeploy waits forever for them.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*' --timeout-graceful-shutdown 5"]
