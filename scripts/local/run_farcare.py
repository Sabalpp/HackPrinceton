#!/usr/bin/env python3
"""Run FarCare's API, sensor delivery and daily sync; optionally start its dashboard."""

import argparse
import fcntl
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from urllib.request import urlopen

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.local._paths import REPO_ROOT, WORKSPACE_ROOT  # noqa: E402

ROOT = WORKSPACE_ROOT


def node_binary():
    configured = shutil.which("node")
    if configured:
        return Path(configured)
    candidates = list((Path.home() / ".nvm/versions/node").glob("*/bin/node"))
    if candidates:
        return max(candidates, key=lambda p: p.stat().st_mtime)
    raise RuntimeError("Node.js is missing. Install Node 20.9+ before starting the dashboard.")


def runtime_settings(python, api):
    # Capture configuration in memory; never log keys or the database connection string.
    script = (
        "import json; from app.config import get_settings; s=get_settings(); "
        'print(json.dumps({"photon":s.photon_enabled,"viewer_token":s.demo_child_token,'
        '"ingest":bool(s.ingest_api_key),"production":s.app_env=="production"}))'
    )
    try:
        result = subprocess.run(
            [str(python), "-c", script],
            cwd=str(api),
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        return json.loads(result.stdout)
    except (subprocess.SubprocessError, ValueError):
        raise RuntimeError(
            "Cannot load the backend configuration. Check backend/.env privately."
        ) from None


def stop_children(children):
    """Stop and reap only process groups created by this launcher."""
    for child in children:
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 10
    for child in children:
        try:
            child.wait(timeout=max(0.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait(timeout=5)


def wait_ready(process, url, *, database=False, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                "A service exited during startup; inspect its log in pipeline_data/."
            )
        try:
            with urlopen(url, timeout=2) as response:
                if database:
                    health = json.load(response)
                    if health.get("status") == "ok" and health.get("database") == "ok":
                        return
                elif response.status == 200:
                    return
        except (OSError, ValueError):
            pass
        time.sleep(1)
    raise RuntimeError("Service readiness timed out. Inspect the log in pipeline_data/.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent-id", default="parent_001")
    parser.add_argument("--subject-id", default="demo")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--web", action="store_true", help="Also start the latest dashboard.")
    parser.add_argument("--open", action="store_true", help="Open the dashboard when it is ready.")
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Use an isolated local copy of saved Fitbit readings; no cloud sync or real messages.",
    )
    parser.add_argument("--web-port", type=int, default=3000)
    parser.add_argument(
        "--days", type=int, default=35, help="Daily projection lookback, including missing dates."
    )
    parser.add_argument(
        "--check", action="store_true", help="Check local configuration without starting services."
    )
    parser.add_argument(
        "--messaging",
        choices=("console", "photon"),
        default="console",
        help="Console simulates delivery. Photon requires configured, opted-in testers.",
    )
    parser.add_argument(
        "--credentials",
        type=Path,
        default=Path.home() / "Downloads/tiger-cloud-db-94181-credentials.env",
    )
    parser.add_argument(
        "--skip-collector",
        action="store_true",
        help="Use an already-running Python sensor/Tiger collector.",
    )
    args = parser.parse_args()
    args.credentials = args.credentials.expanduser().resolve()
    if not all(1024 <= port <= 65535 for port in (args.port, args.web_port)):
        parser.error("Ports must be between 1024 and 65535.")
    if args.web and args.port == args.web_port:
        parser.error("API and dashboard need different ports.")
    if not 1 <= args.days <= 366:
        parser.error("Days must be between 1 and 366.")
    api = REPO_ROOT / "backend"
    python = api / ".venv/bin/python"
    if args.open and not args.web:
        parser.error("--open requires --web.")
    if args.preview and args.messaging != "console":
        parser.error("Local preview supports simulated console messaging only.")
    if not python.exists() or (not args.preview and not (api / ".env").exists()):
        parser.error("FarCare environment is missing. See PIPELINE.md.")
    if not args.preview and not args.skip_collector and not args.credentials.is_file():
        parser.error("Tiger credentials file missing; pass --credentials /path/to/file.env.")
    try:
        configuration = (
            {
                "production": False,
                "ingest": True,
                "photon": False,
                "viewer_token": "dev-child-token",
            }
            if args.preview
            else runtime_settings(python, api)
        )
        if configuration["production"]:
            raise RuntimeError(
                "This is the local demo launcher. Use the deployment runbook for production."
            )
        if not configuration["ingest"]:
            raise RuntimeError("Configure INGEST_API_KEY in backend/.env before starting.")
        if args.messaging == "photon" and not configuration["photon"]:
            raise RuntimeError(
                "Photon is not configured. Add PHOTON_API_KEY and PHOTON_PROJECT_ID privately to backend/.env."
            )
        web = REPO_ROOT / "frontend"
        node = node_binary() if args.web else None
        if args.web and not (web / "node_modules/next/dist/bin/next").is_file():
            raise RuntimeError(
                "Dashboard dependencies are missing. Run npm ci in app_repo/frontend."
            )
        if args.preview and not (ROOT / "pipeline_data/farcare_source_rows.json").is_file():
            raise RuntimeError(
                "Saved Fitbit data is missing. Use the live launcher after syncing Tiger."
            )
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 1
    if args.check:
        print("Local configuration checked; no services, purchases or messages were started.")
        print("Dashboard: " + ("ready to launch" if args.web else "not requested"))
        print(
            "Messaging: "
            + args.messaging
            + (
                " (simulated)"
                if args.messaging == "console"
                else " (requires tester assignments and opt-in)"
            )
        )
        print(
            "Data: "
            + (
                "saved Fitbit snapshot; cloud sync paused"
                if args.preview
                else "Tiger + Google sync"
            )
        )
        print("Browser rendering requires starting the services in your Mac Terminal.")
        return 0
    destination = ROOT / "pipeline_data"
    destination.mkdir(exist_ok=True, mode=0o700)
    lock = (destination / "farcare_launcher.lock").open("a+")
    os.chmod(lock.name, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        print("FarCare is already running through this launcher.", file=sys.stderr)
        return 1
    children = []
    logs = []

    def stop_requested(_signum, _frame):
        raise KeyboardInterrupt

    old_handlers = {
        sig: signal.signal(sig, stop_requested) for sig in (signal.SIGTERM, signal.SIGHUP)
    }
    api_env = dict(
        os.environ,
        FARCARE_WORKSPACE=str(ROOT),
        BACKGROUND_TASKS_ENABLED="true",
        PROACTIVE_CHECKINS_ENABLED="false",
        SEED_HEALTH_DATA="false",
        MESSAGING_CHANNEL=args.messaging,
        PHOTON_DELIVERY="poll" if args.messaging == "photon" else "off",
        CORS_ORIGINS=f"http://localhost:{args.web_port},http://127.0.0.1:{args.web_port}",
    )
    if args.preview:
        # APP_ENV=test disables reading the live .env entirely. The separate DB
        # contains real saved readings plus sample people, never live app state.
        api_env.update(
            APP_ENV="test",
            DATABASE_URL="sqlite:///" + str(destination / "farcare_preview.sqlite3"),
            SEED_DEMO_DATA="false",
            DEMO_MODE="true",
            PHOTON_API_KEY="",
            PHOTON_PROJECT_ID="",
            GEMINI_API_KEY="",
            DEMO_CHILD_TOKEN="dev-child-token",
            DEMO_PARENT_TOKEN="dev-parent-token",
            DEMO_ADMIN_TOKEN="dev-admin-token",
            INGEST_API_KEY="",
            DEMO_PARENT_HANDLE="+15550100001",
            DEMO_CHILD_HANDLE="+15550100002",
            DEMO_RESPONDER_HANDLE="+15550100003",
            DEMO_RESPONDER2_HANDLE="+15550100004",
            DEMO_EMERGENCY_NUMBER="",
            PHOTON_WEBHOOK_SECRET="",
            PHOTON_DELIVERY="off",
            MESSAGING_CHANNEL="console",
        )

    def start(command, name, executable=None, cwd=None, env=None):
        fd = os.open(str(destination / name), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        stream = os.fdopen(fd, "w")
        logs.append(stream)
        os.fchmod(stream.fileno(), 0o600)
        child = subprocess.Popen(
            [str(executable or python)] + command,
            cwd=str(cwd or api),
            env=env or api_env,
            stdout=stream,
            stderr=stream,
            start_new_session=True,
        )
        children.append(child)
        return child

    try:
        if args.preview:
            prepared = subprocess.run(
                [str(python), "-B", str(REPO_ROOT / "scripts/local/prepare_farcare_preview.py")],
                cwd=str(ROOT),
                env=api_env,
                timeout=60,
            )
            if prepared.returncode:
                raise RuntimeError(
                    "Could not prepare the saved-data preview. The live database was not changed."
                )
        server = start(
            ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(args.port)],
            "farcare_api.log",
        )
        url = f"http://127.0.0.1:{args.port}"
        print("Starting FarCare. Server and sync logs are in pipeline_data/.", flush=True)
        wait_ready(server, url + "/health", database=True)
        if not args.preview and not args.skip_collector:
            start(
                [
                    "-m",
                    "farcare_collector.services.health_pipeline",
                    "watch",
                    "--credentials",
                    str(args.credentials),
                    "--subject",
                    args.subject_id,
                ],
                "sensor_collector.log",
                ROOT / ".venv/bin/python",
                cwd=REPO_ROOT / "backend/collector",
            )
        if not args.preview:
            start(
                [
                    "-m",
                    "app.health.sync_tiger",
                    "--subject-id",
                    args.subject_id,
                    "--parent-id",
                    args.parent_id,
                    "--days",
                    str(args.days),
                    "--api-url",
                    url,
                    "--watch-seconds",
                    "60",
                ],
                "farcare_sync.log",
            )
        if args.web:
            web_env = dict(
                os.environ,
                NEXT_PUBLIC_USE_MOCKS="false",
                NEXT_PUBLIC_API_URL=f"http://localhost:{args.port}",
                NEXT_PUBLIC_LOCAL_PREVIEW="true" if args.preview else "false",
                NEXT_TELEMETRY_DISABLED="1",
                PATH=str(node.parent) + os.pathsep + os.environ.get("PATH", ""),
            )
            frontend = start(
                [
                    str(web / "node_modules/next/dist/bin/next"),
                    "dev",
                    "--hostname",
                    "127.0.0.1",
                    "--port",
                    str(args.web_port),
                ],
                "farcare_web.log",
                node,
                cwd=web,
                env=web_env,
            )
            wait_ready(frontend, f"http://127.0.0.1:{args.web_port}", timeout=90)
            print(
                f"Choose Parent, Family, or Helper: http://localhost:{args.web_port}/login",
                flush=True,
            )
            if args.open:
                if not webbrowser.open(f"http://localhost:{args.web_port}/login"):
                    print("Open the dashboard URL above in your browser.", flush=True)
        print("FarCare API: " + url + "/docs", flush=True)
        print("Care workflow console: " + url + "/console", flush=True)
        print(
            "Messaging: "
            + args.messaging
            + (
                " (simulated)"
                if args.messaging == "console"
                else " (enabled; verify receipt on each tester phone)"
            ),
            flush=True,
        )
        if args.preview:
            print(
                "LOCAL PREVIEW: saved Fitbit readings, sample family, simulated messages. Cloud sync is paused.",
                flush=True,
            )
        else:
            print(
                "Sensor delivery targets every 1 second, Google refresh every 15 minutes, and daily app sync every 60 seconds.",
                flush=True,
            )
            print(
                "Daily dates use the selected parent profile timezone. Sync logs confirm ingestion separately.",
                flush=True,
            )
            print("Keep the BLE recorder running.", flush=True)
        print("Ctrl+C stops the processes launched here.", flush=True)
        while all(child.poll() is None for child in children):
            time.sleep(1)
        raise RuntimeError("A backend process exited; inspect pipeline_data/farcare_*.log.")
    except KeyboardInterrupt:
        return 130
    except (RuntimeError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1
    finally:
        stop_children(children)
        for stream in logs:
            stream.close()
        lock.close()
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    sys.exit(main())
