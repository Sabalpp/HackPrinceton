#!/usr/bin/env python3
"""Read synced Google Health records via local OAuth; Python standard library only."""

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import sys
import time
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen

from farcare_collector.paths import WORKSPACE_ROOT

ROOT = WORKSPACE_ROOT
REDIRECT = "http://localhost:3000/auth/callback"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API_URL = "https://health.googleapis.com/v4/users/me/dataTypes"
SCOPES = [
    f"https://www.googleapis.com/auth/googlehealth.{s}.readonly"
    for s in ("activity_and_fitness", "health_metrics_and_measurements", "sleep", "irn", "location")
]
TOKEN_FILE = ROOT / ".google-health" / "tokens.json"
TYPES = {
    "heart-rate": "sample_time.physical_time",
    "daily-resting-heart-rate": "date",
    "heart-rate-variability": "sample_time.physical_time",
    "daily-heart-rate-variability": "date",
    "oxygen-saturation": "sample_time.physical_time",
    "daily-oxygen-saturation": "date",
    "daily-respiratory-rate": "date",
    "daily-sleep-temperature-derivations": "date",
    "sleep": "interval.end_time",
    "steps": "interval.start_time",
    "distance": "interval.start_time",
    "active-zone-minutes": "interval.start_time",
    "exercise": "interval.civil_start_time",
    # No documented IRN time-filter example: preserve all returned alert history.
    "irregular-rhythm-notification": None,
}


def utc_now():
    return datetime.now(timezone.utc)


def stamp(dt):
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def private_json(path, value):
    private_bytes(path, (json.dumps(value, indent=2) + "\n").encode())


def private_bytes(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(".tmp")
    fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(value)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def load_credentials(explicit=None):
    if explicit:
        candidates = [Path(explicit).expanduser()]
    else:
        candidates = list(ROOT.glob("client_secret*.json"))
        candidates += list((Path.home() / "Downloads").glob("client_secret*.json"))
    matches = []
    for path in candidates:
        try:
            config = json.loads(path.read_text()).get("web", {})
        except (OSError, ValueError):
            if explicit:
                raise RuntimeError("Cannot read the specified credentials JSON.")
            continue
        if (
            config.get("client_id")
            and config.get("client_secret")
            and REDIRECT in config.get("redirect_uris", [])
        ):
            matches.append(config)
    if len(matches) != 1:
        raise RuntimeError(
            f"Found {len(matches)} matching OAuth clients. Download the Web application JSON "
            f"with redirect {REDIRECT}, or pass --credentials /path/to/client_secret.json."
        )
    return matches[0]


def request_json(url, token=None, form=None):
    return json.loads(request_bytes(url, token, form))


def request_bytes(url, token=None, form=None, accept="application/json"):
    headers = {"Accept": accept}
    body = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if form is not None:
        body = urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = Request(url, data=body, headers=headers)
    try:
        with urlopen(request, timeout=45) as response:
            return response.read()
    except HTTPError as exc:
        # Google error bodies are retained; credentials and request headers are never logged.
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from None
    except URLError as exc:
        raise RuntimeError(f"Network error: {exc.reason}") from None


def parse_callback(path, expected_state):
    parsed = urlsplit(path)
    if parsed.path != "/auth/callback":
        raise ValueError("Not an OAuth callback")
    query = parse_qs(parsed.query)
    states = query.get("state", [])
    if len(states) != 1 or not secrets.compare_digest(states[0], expected_state):
        raise ValueError("OAuth state mismatch; callback rejected")
    if query.get("error"):
        raise RuntimeError(f"Google authorization error: {query['error'][0]}")
    codes = query.get("code", [])
    if len(codes) != 1:
        raise ValueError("Missing or ambiguous authorization code")
    return codes[0]


def authorize(config, no_browser=False):
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    result = {}

    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # Do not log callback URLs containing authorization codes.

        def do_GET(self):
            try:
                code = parse_callback(self.path, state)
            except ValueError:
                self.send_error(400, "Invalid callback. Use the current Google sign-in link.")
                return
            except RuntimeError as exc:
                result["error"] = str(exc)
                message = "Authorization was declined. Return to Terminal."
            else:
                result["code"] = code
                message = "Google authorization received. Return to Terminal for download progress."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(message.encode())

    class LocalServer(HTTPServer):
        def get_request(self):
            connection, address = super().get_request()
            connection.settimeout(5)
            return connection, address

    try:
        server = LocalServer(("127.0.0.1", 3000), Callback)
    except OSError as exc:
        raise RuntimeError(
            f"Cannot start localhost:3000: {exc}. Close any other server on port 3000; "
            "if this is an execution restriction, run this command in Mac Terminal."
        ) from None
    url = (
        AUTH_URL
        + "?"
        + urlencode(
            {
                "client_id": config["client_id"],
                "redirect_uri": REDIRECT,
                "response_type": "code",
                "scope": " ".join(SCOPES),
                "access_type": "offline",
                "prompt": "select_account consent",
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
    )
    with server:
        server.timeout = 1
        print(
            "Sign in with the Google account used in Google Health. The five read-only permissions cover "
            "activity, vitals, sleep, recorded rhythm alerts, and exercise GPS exports.",
            flush=True,
        )
        print("\n" + url + "\n", flush=True)
        if not no_browser:
            webbrowser.open(url)
        deadline = time.monotonic() + 600
        while not result and time.monotonic() < deadline:
            server.handle_request()
    if result.get("error"):
        raise RuntimeError(result["error"])
    if not result.get("code"):
        raise RuntimeError("Google sign-in timed out after 10 minutes. Rerun to try again.")
    return request_json(
        TOKEN_URL,
        form={
            "client_id": config["client_id"],
            "client_secret": config["client_secret"],
            "code": result["code"],
            "grant_type": "authorization_code",
            "redirect_uri": REDIRECT,
            "code_verifier": verifier,
        },
    )


def get_tokens(config, reauthorize=False, no_browser=False):
    cached = json.loads(TOKEN_FILE.read_text()) if TOKEN_FILE.exists() else {}
    if cached.get("client_id") != config["client_id"]:
        cached = {}
    missing = set(SCOPES) - set(cached.get("scope", "").split())
    if reauthorize or not cached.get("refresh_token") or missing:
        fresh = authorize(config, no_browser)
        # Never retain tokens from a different account after a fresh login.
        cached = {}
    elif cached.get("expires_at", 0) > time.time() + 120:
        return cached
    else:
        fresh = request_json(
            TOKEN_URL,
            form={
                "client_id": config["client_id"],
                "client_secret": config["client_secret"],
                "refresh_token": cached["refresh_token"],
                "grant_type": "refresh_token",
            },
        )
    if not fresh.get("access_token"):
        raise RuntimeError("Google token response has no access_token.")
    tokens = {
        **cached,
        **fresh,
        "client_id": config["client_id"],
        "expires_at": time.time() + int(fresh.get("expires_in", 3600)),
    }
    private_json(TOKEN_FILE, tokens)
    missing = set(SCOPES) - set(tokens.get("scope", "").split())
    if missing:
        print(
            "Some permissions were not granted; affected API requests may fail. Use --reauthorize to retry.",
            flush=True,
        )
    return tokens


def query_parameters(data_type, start, end):
    field = TYPES[data_type]
    if field is None:
        return {"pageSize": 25, "dataSourceFamily": "users/me/dataSourceFamilies/google-wearables"}
    if field == "date" or field == "interval.civil_start_time":
        lower = start.astimezone().date().isoformat()
        upper = (end.astimezone().date() + timedelta(days=1)).isoformat()
    else:
        lower, upper = stamp(start), stamp(end)
    key = data_type.replace("-", "_") + "." + field
    params = {
        "pageSize": 25 if data_type in ("sleep", "exercise") else 10000,
        "filter": f'{key} >= "{lower}" AND {key} < "{upper}"',
    }
    if data_type != "sleep":
        params["dataSourceFamily"] = "users/me/dataSourceFamilies/google-wearables"
    return params


def fetch_type(data_type, token, start, end, destination):
    params = query_parameters(data_type, start, end)
    count = 0
    seen = set()
    for page in range(1, 1001):
        response = request_json(
            API_URL + "/" + data_type + "/dataPoints?" + urlencode(params), token=token
        )
        private_json(destination / data_type / f"page-{page:05d}.json", response)
        count += len(response.get("dataPoints", []))
        next_page = response.get("nextPageToken")
        if not next_page:
            return {
                "status": "ok" if count else "empty",
                "records": count,
                "pages": page,
                "query": query_parameters(data_type, start, end),
            }
        if next_page in seen:
            raise RuntimeError("API repeated a pagination token; incomplete download stopped.")
        seen.add(next_page)
        params["pageToken"] = next_page
    raise RuntimeError(
        "Pagination safety limit reached; download is incomplete. Try a shorter date range."
    )


def export_exercises(token, destination):
    """Download documented TCX exports, with GPS only when the workout has it."""
    exported, failures, seen = [], [], set()
    for path in sorted((destination / "exercise").glob("page-*.json")):
        for point in json.loads(path.read_text()).get("dataPoints", []):
            name = point.get("name", "")
            if name in seen:
                continue
            seen.add(name)
            if not re.fullmatch(
                r"users/(?:me|[0-9]+)/dataTypes/exercise/dataPoints/[A-Za-z0-9_-]+", name
            ):
                failures.append(
                    {"error": "Missing or unexpected exercise resource name; export skipped."}
                )
                continue
            identifier = name.rsplit("/", 1)[-1]
            url = (
                "https://health.googleapis.com/v4/"
                + name
                + ":exportExerciseTcx?alt=media&partialData=true"
            )
            try:
                content = request_bytes(url, token=token, accept="application/xml")
                if not content.strip():
                    raise RuntimeError("Google returned an empty TCX export.")
                private_bytes(destination / "exercise-tcx" / f"{identifier}.tcx", content)
                exported.append(identifier)
            except RuntimeError as exc:
                failures.append({"exercise": identifier, "error": str(exc)})
                print(f"Exercise TCX {identifier}: ERROR {exc}", file=sys.stderr, flush=True)
    return {
        "status": "partial" if failures else ("ok" if exported else "empty"),
        "files": len(exported),
        "errors": failures,
        "note": "TCX may contain HR/activity without GPS. No route is invented when GPS is absent.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials", help="Downloaded Google Web application credentials JSON")
    parser.add_argument("--days", type=int, default=7, help="Lookback in days (default: 7)")
    parser.add_argument("--reauthorize", action="store_true")
    parser.add_argument(
        "--no-browser", action="store_true", help="Print login URL without opening the browser"
    )
    parser.add_argument(
        "--check", action="store_true", help="Validate credentials only; no network or login"
    )
    args = parser.parse_args()
    if not 1 <= args.days <= 90:
        parser.error("--days must be between 1 and 90")
    config = load_credentials(args.credentials)
    print("OAuth credentials found; localhost callback matches.", flush=True)
    if args.check:
        return 0
    tokens = get_tokens(config, args.reauthorize, args.no_browser)
    end = utc_now()
    start = end - timedelta(days=args.days)
    destination = ROOT / "api_data" / end.strftime("%Y%m%dT%H%M%S%fZ")
    report = {
        "started_at": stamp(end),
        "lookback_days": args.days,
        "note": "Synced API records, not live BLE or unprocessed optical sensor samples. "
        "Sleep includes all sources; other types are filtered to Google/Fitbit wearables. "
        "Daily and exercise queries include complete boundary calendar dates. "
        "Irregular rhythm notifications include all available alert history without a date filter.",
        "types": {},
    }
    print(
        f"Downloading synced records from the last {args.days} days, plus available rhythm alert history ...",
        flush=True,
    )
    errors = 0
    for data_type in TYPES:
        try:
            entry = fetch_type(data_type, tokens["access_token"], start, end, destination)
            print(f"{data_type}: {entry['records']} records ({entry['status']})", flush=True)
        except RuntimeError as exc:
            errors += 1
            entry = {"status": "error", "error": str(exc), "note": "Any saved pages are partial."}
            print(f"{data_type}: ERROR {exc}", file=sys.stderr, flush=True)
        report["types"][data_type] = entry
        private_json(destination / "summary.json", report)
    routes = export_exercises(tokens["access_token"], destination)
    report["exercise_tcx"] = routes
    errors += len(routes["errors"])
    private_json(destination / "summary.json", report)
    print(f"Exercise TCX exports: {routes['files']} files ({routes['status']})", flush=True)
    print(f"\nSaved original API response pages and summary to: {destination}", flush=True)
    if errors:
        print(
            "PARTIAL: see exact errors in summary.json. A 403 may mean the API or scopes need enabling. "
            "For missing permissions, fix Google Cloud Data Access and rerun with --reauthorize.",
            flush=True,
        )
        return 1
    if not any(row.get("records") for row in report["types"].values()):
        print(
            "All responses were empty. Sync your Air in Google Health and verify the signed-in account.",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nStopped. Any completed response pages remain saved.")
        sys.exit(130)
    except Exception as exc:
        print(f"ERROR {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
