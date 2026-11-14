"""Exercise the real deployment images against disposable, synthetic PostgreSQL.

Run from the repository root after building farcare-cloudflare-{api,web}:ci.
No developer .env or external service credentials are read. Every Docker resource
has a unique test-owned name and is removed even when a check fails.
"""

import json
import secrets
import subprocess
import time
from http.cookies import SimpleCookie
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

ORIGIN = "https://farcare-ci.synthetic.workers.dev"


def command(*args, timeout=60, input=None, check=True, diagnostics=False):
    result = subprocess.run(
        list(args),
        input=input,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if check and result.returncode:
        # Arguments may contain the disposable database password; don't print them.
        raise RuntimeError(
            f"{args[0]} {args[1]} failed: {result.stdout}{result.stderr}"
        )
    return (result.stdout + result.stderr if diagnostics else result.stdout).strip()


def request(url, *, data=None, headers=None):
    headers = {"Origin": ORIGIN, **(headers or {})}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = Request(
        url,
        data=json.dumps(data).encode() if data is not None else None,
        headers=headers,
    )
    try:
        response = urlopen(req, timeout=3)
    except HTTPError as exc:
        response = exc
    with response:
        return response.status, response.headers, response.read()


def wait_ready(origin, commit, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            status, _, body = request(origin + "/health")
            health = json.loads(body)
            ready_status, _, ready_body = request(origin + "/ready")
            if (
                status == 200
                and health["database"] == "ok"
                and health["commit"] == commit
                and health["environment"] == "production"
                and health["demo_mode"] is False
                and health["messaging"] == "console"
                and health["photon_delivery"] == "off"
                and health["ai"] == "fallback"
                and ready_status == 200
                and json.loads(ready_body)["ready"] is True
            ):
                return
        except (OSError, URLError, ValueError, KeyError):
            pass
        time.sleep(1)
    raise RuntimeError("Production API/database/standalone worker did not become ready")


def address(container, port):
    binding = command("docker", "port", container, f"{port}/tcp")
    if not binding.startswith("127.0.0.1:") or "\n" in binding:
        raise RuntimeError("Smoke container must only expose its port on loopback")
    return "http://" + binding


def main():
    suffix = uuid4().hex[:12]
    network = f"farcare-smoke-{suffix}"
    pg, api, web = (f"{network}-{kind}" for kind in ("postgres", "api", "web"))
    commit = command("git", "rev-parse", "HEAD")
    password = secrets.token_urlsafe(32)
    containers = []
    network_created = False
    try:
        # A private test bridge supports host-port publication. Docker's internal
        # network mode can omit these mappings; every published port below is
        # bound only to loopback. No provider credentials enter the containers.
        command("docker", "network", "create", network)
        network_created = True
        containers.append(pg)
        command(
            "docker",
            "run",
            "-d",
            "--name",
            pg,
            "--network",
            network,
            "--network-alias",
            "postgres",
            "--tmpfs",
            "/var/lib/postgresql/data",
            "-e",
            "POSTGRES_USER=farcare",
            "-e",
            "POSTGRES_DB=farcare_ci",
            "-e",
            f"POSTGRES_PASSWORD={password}",
            "postgres:16",
            timeout=180,
        )
        for _ in range(60):
            probe = command(
                "docker",
                "exec",
                pg,
                "pg_isready",
                "-h",
                "127.0.0.1",
                "-U",
                "farcare",
                "-d",
                "farcare_ci",
                check=False,
            )
            if "accepting connections" in probe:
                break
            time.sleep(1)
        else:
            raise RuntimeError("Synthetic PostgreSQL did not start")

        # TLS is deliberately off only inside this disposable CI bridge. The
        # deployment configurator independently requires TLS for a hosted DB.
        env = {
            "APP_ENV": "production",
            "GIT_COMMIT": commit,
            "DATABASE_URL": f"postgresql://farcare:{password}@postgres/farcare_ci?sslmode=disable",
            "INGEST_API_KEY": secrets.token_urlsafe(32),
            "CORS_ORIGINS": ORIGIN,
            "DEMO_MODE": "false",
            "SEED_DEMO_DATA": "false",
            "SEED_HEALTH_DATA": "false",
            "BACKGROUND_TASKS_ENABLED": "false",
            "REGISTRATION_ENABLED": "false",
            "MESSAGING_CHANNEL": "console",
            "PHOTON_DELIVERY": "off",
            "PROACTIVE_CHECKINS_ENABLED": "false",
            "TIGER_SYNC_ENABLED": "false",
            "WORKER_INTERVAL_SECONDS": "1",
        }
        containers.append(api)
        env_args = [
            item for key, value in env.items() for item in ("-e", f"{key}={value}")
        ]
        command(
            "docker",
            "run",
            "-d",
            "--name",
            api,
            "--network",
            network,
            "-p",
            "127.0.0.1::8000",
            *env_args,
            "farcare-cloudflare-api:ci",
        )
        api_origin = address(api, 8000)
        wait_ready(api_origin, commit)
        assert command("docker", "exec", api, "id", "-u") != "0", "API ran as root"
        assert request(api_origin + "/api/me")[0] == 401, "Anonymous data was exposed"
        assert (
            request(
                api_origin + "/api/me",
                headers={"Authorization": "Bearer dev-child-token"},
            )[0]
            == 401
        ), "A development credential worked in production"
        assert request(api_origin + "/api/auth/demo", data={"role": "child"})[0] == 404
        print("PASS: production API, PostgreSQL, worker readiness and demo/auth guards")

        # Exercise the operator provisioning path and production session flags.
        account_password = secrets.token_urlsafe(24)
        source = (
            "from app.manage_accounts import create_owner\n"
            "create_owner('synthetic-owner@example.invalid', 'Synthetic Owner', "
            f"{account_password!r})\n"
        )
        command("docker", "exec", "-i", api, "/app/.venv/bin/python", "-", input=source)
        status, headers, body = request(
            api_origin + "/api/auth/login",
            data={
                "email": "synthetic-owner@example.invalid",
                "password": account_password,
            },
        )
        session = json.loads(body)
        assert status == 200 and session["authenticated"] is True
        assert session["person"]["role"] == "child" and session["csrf_token"]
        cookie = SimpleCookie()
        cookie.load(headers["Set-Cookie"])
        token = cookie["farcare_session"]
        assert token["secure"] and token["httponly"] and token["samesite"] == "lax"
        session_headers = {"Cookie": f"farcare_session={token.value}"}
        assert request(api_origin + "/api/me", headers=session_headers)[0] == 200
        assert (
            request(api_origin + "/api/auth/logout", data={}, headers=session_headers)[
                0
            ]
            == 403
        )
        print("PASS: operator-created account, secure session and CSRF enforcement")

        containers.append(web)
        command(
            "docker",
            "run",
            "-d",
            "--name",
            web,
            "--network",
            network,
            "-p",
            "127.0.0.1::3000",
            "farcare-cloudflare-web:ci",
        )
        web_origin = address(web, 3000)
        for _ in range(60):
            try:
                status, headers, body = request(web_origin + "/login")
                if (
                    status == 200
                    and "text/html" in headers["Content-Type"]
                    and b"FarCare" in body
                ):
                    break
            except (OSError, URLError):
                pass
            time.sleep(1)
        else:
            raise RuntimeError("Production frontend did not serve the login screen")
        assert command("docker", "exec", web, "id", "-u") != "0", "Frontend ran as root"
        print("PASS: production frontend serves /login as a non-root user")

        # Kill only this test container's worker. The supervisor must stop the API
        # and exit unsuccessfully so the platform can recreate a healthy unit.
        stop_worker = """
import os, pathlib, signal
for path in pathlib.Path('/proc').glob('[0-9]*/cmdline'):
    try:
        args = path.read_bytes().rstrip(b'\\0').split(b'\\0')
        if args[-2:] == [b'-m', b'app.worker']:
            os.kill(int(path.parent.name), signal.SIGTERM)
            break
    except (FileNotFoundError, ProcessLookupError):
        continue
else:
    raise SystemExit('Test worker process was not found')
"""
        command(
            "docker", "exec", "-i", api, "/app/.venv/bin/python", "-", input=stop_worker
        )
        assert command("docker", "wait", api, timeout=40) == "1", (
            "Supervisor did not fail closed"
        )
        command("docker", "start", api)
        # Docker may allocate a different host port when restarting the container.
        api_origin = address(api, 8000)
        wait_ready(api_origin, commit)
        assert request(api_origin + "/api/me", headers=session_headers)[0] == 200
        print(
            "PASS: worker failure stops API; restart restores readiness and database-backed session"
        )
        return 0
    except Exception:
        # Only these fresh synthetic containers are ever logged, never a user's app.
        for name in containers:
            print(f"Synthetic container diagnostics: {name}", flush=True)
            print(
                command(
                    "docker",
                    "logs",
                    "--tail",
                    "100",
                    name,
                    check=False,
                    diagnostics=True,
                )
            )
        raise
    finally:
        for name in reversed(containers):
            command("docker", "rm", "-f", "-v", name, check=False)
        if network_created:
            command("docker", "network", "rm", network, check=False)


if __name__ == "__main__":
    raise SystemExit(main())
