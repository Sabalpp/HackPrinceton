"""Replay a labeled IMU trace through the fall detector and POST the event to the API.

This plays the role of an edge device: detection runs here, and only a `fall.suspected`
event (never raw sensor data) is sent to the backend.

    uv run python scripts/replay_fall.py --api http://localhost:8000 --trace fall
    uv run python scripts/replay_fall.py --trace walking        # no event is sent
    uv run python scripts/replay_fall.py --file my_trace.json   # [[ax, ay, az], ...] in g @ 50 Hz

The ingest key is read from INGEST_API_KEY. All replayed events are labeled simulated.
"""

import argparse
import json
import os
import secrets
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.fall_detection import detector, traces  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--api", default=os.environ.get("FARCARE_API_URL", "http://localhost:8000"))
    ap.add_argument("--parent-id", default="parent_001")
    ap.add_argument("--trace", choices=traces.KINDS, default="fall")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--file", type=Path, help="JSON list of [ax, ay, az] samples (g, 50 Hz)")
    args = ap.parse_args()

    if args.file:
        samples = [tuple(s) for s in json.loads(args.file.read_text())]
        source = "replayed_dataset"
    else:
        samples = traces.generate(args.trace, args.seed)
        source = "simulated_imu"
    result = detector.classify(samples)
    print(f"detector: {result.label} (score {result.score}) features={result.features}")
    if result.label != "possible_fall":
        print("No possible fall detected; nothing sent.")
        return 0

    event = {
        "event_id": f"replay_{secrets.token_hex(6)}",
        "parent_id": args.parent_id,
        "event_type": "fall.suspected",
        "source": source,
        "timestamp": datetime.now(UTC).isoformat(),
        "confidence": result.score,
        "data_mode": "simulated",
    }
    headers = {}
    if key := os.environ.get("INGEST_API_KEY"):
        headers["X-FarCare-Ingest-Key"] = key
    resp = httpx.post(f"{args.api}/api/events/fall", json=event, headers=headers, timeout=15)
    print(resp.status_code, resp.text)
    return 0 if resp.is_success else 1


if __name__ == "__main__":
    raise SystemExit(main())
