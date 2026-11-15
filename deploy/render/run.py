"""Supervise the existing API, worker and dashboard in one native web service.

Render Free sleeps: all three processes pause together. PostgreSQL keeps the
queues and records; this launcher does not provide always-on timer guarantees.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
API_ORIGIN = "http://127.0.0.1:8000"


def settings(environ):
    env = dict(environ)
    port = int(env.get("PORT", "10000"))
    if not 1024 <= port <= 65535 or port == 8000:
        raise ValueError("The public port must be distinct from the private API port")
    if not env.get("CORS_ORIGINS"):
        origin = env.get("RENDER_EXTERNAL_URL", "").rstrip("/")
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
            or "*" in origin
        ):
            raise ValueError("Set CORS_ORIGINS to the exact HTTPS dashboard origin")
        env["CORS_ORIGINS"] = origin
    # One standalone worker; never start a second scheduler inside the API.
    env["BACKGROUND_TASKS_ENABLED"] = "false"
    env["FARCARE_API_ORIGIN"] = API_ORIGIN
    env["NEXT_PUBLIC_API_URL"] = "/backend"
    env["NEXT_PUBLIC_USE_MOCKS"] = "false"
    env["NEXT_PUBLIC_LOCAL_PREVIEW"] = "false"
    env["NODE_ENV"] = "production"
    return env, port


def wait_api(process, stop):
    deadline = time.monotonic() + 90
    while not stop.is_set():
        if process.poll() is not None or time.monotonic() >= deadline:
            raise RuntimeError("API startup failed")
        try:
            with urlopen(API_ORIGIN + "/health", timeout=2) as response:
                if json.load(response).get("database") == "ok":
                    return
        except (OSError, ValueError):
            pass
        stop.wait(0.25)


def stop_children(children):
    # Signal only process groups created here, including descendants of Next.
    for child in reversed(children):
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 20
    for child in reversed(children):
        try:
            child.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()


def main():
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    children = []
    try:
        env, port = settings(os.environ)
        node = shutil.which("node")
        if not node:
            raise RuntimeError("Node is unavailable")
        python = str(ROOT / "backend/.venv/bin/python")
        api_dir = ROOT / "backend"
        web_dir = ROOT / "frontend"
        plans = [
            (
                api_dir,
                [
                    python,
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8000",
                    "--proxy-headers",
                    "--forwarded-allow-ips",
                    "127.0.0.1",
                    "--no-access-log",
                    "--timeout-graceful-shutdown",
                    "5",
                ],
            ),
            (api_dir, [python, "-m", "app.worker"]),
            (
                web_dir,
                [
                    node,
                    "node_modules/next/dist/bin/next",
                    "start",
                    "--hostname",
                    "0.0.0.0",
                    "--port",
                    str(port),
                ],
            ),
        ]
        for cwd, command in plans:
            if stop.is_set():
                return 0
            children.append(
                subprocess.Popen(command, cwd=cwd, env=env, start_new_session=True)
            )
            if len(children) == 1:
                # Schema initialization finishes before the worker can touch the DB.
                wait_api(children[0], stop)
        while not stop.wait(0.5):
            if any(child.poll() is not None for child in children):
                raise RuntimeError("A supervised process exited")
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        # Do not print secrets, URLs or exception details from configuration.
        print(
            "FarCare host stopped: " + type(exc).__name__, file=sys.stderr, flush=True
        )
        return 1
    finally:
        stop_children(children)


if __name__ == "__main__":
    raise SystemExit(main())
