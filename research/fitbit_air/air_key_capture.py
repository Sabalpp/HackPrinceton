#!/usr/bin/env python3
"""mitmproxy addon: capture the Fitbit Air DTLS pre-shared keys from app traffic.

The official Google Health app fetches per-device pre-shared keys (PSKs) from
Google's cloud over the user's own authenticated session. The band's live
Bluetooth stream (DTLS 1.2, TLS_PSK_WITH_AES_128_CCM) is encrypted with those
keys. This addon watches the app's HTTPS traffic through mitmproxy and saves the
keys so the offline decoder can read the band's CoAP sensor channel.

It never injects, replays, or writes anything. It only reads responses that the
app itself requested, for a band you own, over your own Google account.

Usage (see README "Air key capture" for full Android setup):
    pip install mitmproxy
    mitmdump -s air_key_capture.py        # or: mitmproxy -s air_key_capture.py
Then route the phone's traffic through this proxy and open Google Health.

Captured keys are written to air_keys.json (owner-only). Every response from a
Fitbit/Google wearable host is noted in air_capture.log so you can confirm the
proxy is actually intercepting (and spot TLS/pinning failures).
"""

import base64
import binascii
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# mitmproxy loads addons under its own synthetic package name, not __main__.
repo_path = str(Path(__file__).resolve().parents[2])
if repo_path not in sys.path:
    sys.path.insert(0, repo_path)

from scripts.local._paths import WORKSPACE_ROOT  # noqa: E402

ROOT = WORKSPACE_ROOT
KEYS_FILE = ROOT / "air_keys.json"
LOG_FILE = ROOT / "air_capture.log"

# Hosts the Google Health app uses for the Fitbit device-key exchange. The key
# fetch is tunnelled through the vestibule gRPC shim; the inner Fitbit REST call
# is /1/user/-/devices/device/{id}/keys.json.
KEY_HOSTS = ("fitbitvestibuleshim-pa.googleapis.com", "fitbit.com", "fitbitapi.com")
INTEREST = ("fitbit", "googlehealth", "vestibule")
DEVICE_ID_RE = re.compile(r"/devices/device/([0-9A-Za-z._-]+)/keys", re.IGNORECASE)
# A flat {"key": "<base64>", "keyId": <id>} object following "primaryKey"/"secondaryKey".
KEY_OBJ_RE = re.compile(r'"(primaryKey|secondaryKey)"\s*:\s*\{(.*?)\}', re.IGNORECASE | re.DOTALL)
FIELD_KEY_RE = re.compile(r'"key"\s*:\s*"([A-Za-z0-9+/=_-]+)"')
FIELD_ID_RE = re.compile(r'"keyId"\s*:\s*"?([0-9A-Za-z]+)"?')


def _now():
    return datetime.now(timezone.utc).isoformat()


def _log(line):
    with LOG_FILE.open("a", buffering=1) as handle:
        handle.write(f"{_now()}  {line}\n")


def _decode_key(b64):
    """Return 16 raw key bytes, or None if this is not a valid 128-bit key."""
    pad = b64 + "=" * (-len(b64) % 4)
    for decoder in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            raw = decoder(pad)
        except (binascii.Error, ValueError):
            continue
        if len(raw) == 16:
            return raw
    return None


def _extract_keys(text):
    """Pull primary/secondary PSKs out of a response body. Returns {} if none."""
    found = {}
    for match in KEY_OBJ_RE.finditer(text):
        name = match.group(1)
        body = match.group(2)
        key_match = FIELD_KEY_RE.search(body)
        id_match = FIELD_ID_RE.search(body)
        if not key_match:
            continue
        raw = _decode_key(key_match.group(1))
        if raw is None:
            continue  # right field name, wrong size: not the DTLS PSK
        found[name] = {
            "key_b64": key_match.group(1),
            "key_hex": raw.hex(),
            "keyId": id_match.group(1) if id_match else None,
        }
    return found


def response(flow):
    host = (flow.request.pretty_host or "").lower()
    if not any(token in host for token in INTEREST) and not any(
        host.endswith(k) for k in KEY_HOSTS
    ):
        return

    try:
        text = flow.response.get_text(strict=False) or ""
    except Exception as exc:  # decoding is best-effort; never kill the proxy
        _log(f"DECODE-FAIL {host} {flow.request.path}  {type(exc).__name__}: {exc}")
        return

    _log(f"SEEN {flow.response.status_code} {host}{flow.request.path}  {len(text)}B")

    if "primaryKey" not in text and "secondaryKey" not in text:
        return

    keys = _extract_keys(text)
    if not keys:
        _log(f"KEY-FIELD but no 16-byte key parsed at {host}{flow.request.path}")
        return

    device_id = None
    id_match = DEVICE_ID_RE.search(flow.request.path)
    if id_match:
        device_id = id_match.group(1)

    record = {
        "captured_at": _now(),
        "source_host": host,
        "source_path": flow.request.path,
        "device_id": device_id,
        "keys": keys,
    }
    KEYS_FILE.write_text(json.dumps(record, indent=2))
    try:
        KEYS_FILE.chmod(0o600)
    except OSError:
        pass

    names = ", ".join(sorted(keys))
    _log(f"CAPTURED keys ({names}) device={device_id} -> {KEYS_FILE.name}")
    print("\n" + "=" * 60)
    print("  FITBIT AIR KEYS CAPTURED")
    print(f"  device: {device_id}")
    for name in sorted(keys):
        print(f"  {name}: keyId={keys[name]['keyId']}  key={keys[name]['key_hex']}")
    print(f"  saved -> {KEYS_FILE}")
    print("=" * 60 + "\n", flush=True)
