"""Run the existing API and care worker as separate supervised OS processes.

If either fails, stop the others and exit. Cloudflare's minute heartbeat starts
the container again. PostgreSQL remains the durable state and worker mutex.
"""

import json
import os
import signal
import subprocess
import sys
import threading
import time
from urllib.request import urlopen


def commands(env):
    result = [
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
            "--proxy-headers",
            "--forwarded-allow-ips",
            "*",
            "--no-access-log",
            "--timeout-graceful-shutdown",
            "5",
        ],
        [sys.executable, "-m", "app.worker"],
    ]
    if env.get("TIGER_SYNC_ENABLED") == "true":
        if not env.get("TIGER_SUBJECT_ID") or not env.get("TIGER_PARENT_ID"):
            raise ValueError("Tiger sync requires explicit subject and parent IDs")
        result.append(
            [
                sys.executable,
                "-m",
                "app.health.sync_tiger",
                "--subject-id",
                env["TIGER_SUBJECT_ID"],
                "--parent-id",
                env["TIGER_PARENT_ID"],
                "--api-url",
                "http://127.0.0.1:8000",
                "--watch-seconds",
                env.get("TIGER_SYNC_SECONDS", "60"),
                "--days",
                env.get("TIGER_LOOKBACK_DAYS", "35"),
            ]
        )
    return result


def main():
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    children = []
    try:
        planned = commands(os.environ)
        children.append(subprocess.Popen(planned[0]))
        deadline = time.monotonic() + 90
        # Start API/database initialization first; avoid concurrent schema creation.
        while not stop.is_set():
            if children[0].poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("API did not become ready")
            try:
                with urlopen("http://127.0.0.1:8000/health", timeout=2) as response:
                    if json.load(response).get("database") == "ok":
                        break
            except (OSError, ValueError):
                pass
            stop.wait(0.25)
        if stop.is_set():
            return 0
        for command in planned[1:]:
            children.append(subprocess.Popen(command))
        while not stop.wait(1):
            if any(child.poll() is not None for child in children):
                raise RuntimeError("A supervised process exited")
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        # Never print process environment, database URLs, or exception bodies.
        print("Cloudflare supervisor stopped: " + type(exc).__name__, flush=True)
        return 1
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
        deadline = time.monotonic() + 25
        for child in reversed(children):
            try:
                child.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == "__main__":
    raise SystemExit(main())
