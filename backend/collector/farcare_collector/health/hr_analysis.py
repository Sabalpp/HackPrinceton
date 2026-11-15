#!/usr/bin/env python3
"""Build an offline, descriptive HR recording review from an immutable snapshot.

No clinical classification, emergency detection, HRV estimate, or data upload is
performed. Review marks describe sustained changes within this recording only.
"""

import argparse
import hashlib
import html
import json
import math
import os
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

HR_MEASUREMENT = "00002a37-0000-1000-8000-00805f9b34fb"


@dataclass(frozen=True)
class Config:
    gap_seconds: float = 3.0
    review_delta_bpm: float = 12.0
    review_mad_multiplier: float = 3.0
    review_min_seconds: float = 30.0
    trend_bin_seconds: float = 60.0

    def validate(self):
        for name, value in asdict(self).items():
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and greater than zero")


def stamp(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_timestamp(value):
    if not isinstance(value, str):
        raise ValueError("timestamp must be a string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp requires an explicit timezone")
    return parsed.astimezone(timezone.utc)


def payload_bpm(value):
    if not isinstance(value, str):
        raise ValueError("payload must be hex text")
    data = bytes.fromhex(value)
    if not data:
        raise ValueError("empty payload")
    width = 2 if data[0] & 1 else 1
    if len(data) < width + 1:
        raise ValueError("truncated BPM field")
    return int.from_bytes(data[1 : width + 1], "little")


def median(values):
    return float(statistics.median(values)) if values else None


def review_events(readings, reference, threshold, config):
    events, current = [], []
    direction = None

    def finish():
        if not current:
            return
        duration = (current[-1]["time"] - current[0]["time"]).total_seconds()
        if duration < config.review_min_seconds:
            return
        values = [row["bpm"] for row in current]
        events.append(
            {
                "start": stamp(current[0]["time"]),
                "end": stamp(current[-1]["time"]),
                "duration_seconds": duration,
                "direction": direction,
                "reading_count": len(current),
                "min_bpm": min(values),
                "max_bpm": max(values),
                "median_bpm": median(values),
                "peak_distance_from_session_median_bpm": max(abs(v - reference) for v in values),
                "interpretation": "Within-recording review mark; cause and clinical significance unknown.",
            }
        )

    previous = None
    for row in readings:
        delta = row["bpm"] - reference
        next_direction = (
            "above" if delta >= threshold else ("below" if delta <= -threshold else None)
        )
        gap = (
            previous is not None
            and (row["time"] - previous["time"]).total_seconds() > config.gap_seconds
        )
        if gap or next_direction != direction:
            finish()
            current = []
        direction = next_direction
        if direction is not None:
            current.append(row)
        previous = row
    finish()
    return events


def analyze_bytes(data, config=None, source_name="source_snapshot.jsonl", generated_at=None):
    config = config or Config()
    config.validate()
    counts = Counter()
    details = []
    valid = []
    previous_time = None

    def issue(line, category, reason, rejected=True):
        counts[category] += 1
        if rejected:
            counts["rejected_rows"] += 1
        if len(details) < 40:
            details.append({"line": line, "category": category, "reason": reason})

    for line_number, line in enumerate(data.splitlines(), 1):
        counts["physical_lines"] += 1
        if not line.strip():
            counts["blank_lines"] += 1
            continue
        counts["input_rows"] += 1
        try:
            record = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            issue(line_number, "invalid_json_rows", "Not a complete UTF-8 JSON record.")
            continue
        if not isinstance(record, dict):
            issue(line_number, "non_object_rows", "JSON record is not an object.")
            continue
        if (
            "characteristic_uuid" in record
            and str(record["characteristic_uuid"]).lower() != HR_MEASUREMENT
        ):
            issue(
                line_number,
                "wrong_characteristic_rows",
                "Record identifies a characteristic other than Heart Rate Measurement.",
            )
            continue
        try:
            timestamp = parse_timestamp(record.get("receipt_timestamp"))
        except (ValueError, TypeError, OverflowError):
            issue(
                line_number,
                "invalid_timestamp_rows",
                "Missing, invalid, or timezone-free receipt timestamp.",
            )
            continue
        bpm = record.get("bpm")
        if (
            isinstance(bpm, bool)
            or not isinstance(bpm, (int, float))
            or not 0 <= bpm <= 65535
            or not math.isfinite(bpm)
            or bpm != int(bpm)
        ):
            issue(
                line_number,
                "invalid_bpm_rows",
                "BPM must be an integer representable in the BLE BPM field.",
            )
            continue
        bpm = int(bpm)
        verified = False
        if "payload_hex" in record:
            counts["payload_present_rows"] += 1
            try:
                decoded = payload_bpm(record["payload_hex"])
            except (ValueError, TypeError):
                issue(
                    line_number,
                    "malformed_payload_rows",
                    "Payload cannot supply a complete BPM field.",
                )
                continue
            if decoded != bpm:
                issue(
                    line_number,
                    "payload_mismatch_rows",
                    "Recorded BPM differs from the decoded payload; row excluded.",
                )
                continue
            verified = True
            counts["payload_matching_rows"] += 1
        else:
            counts["payload_missing_rows"] += 1
        if bpm == 0:
            issue(
                line_number,
                "zero_bpm_rows",
                "Zero-valued measurement retained in snapshot but excluded from descriptive pulse statistics.",
            )
            continue
        if previous_time is not None and timestamp < previous_time:
            issue(
                line_number,
                "out_of_order_rows",
                "Arrival order goes backward; analysis sorts by receipt time.",
                rejected=False,
            )
        previous_time = timestamp
        valid.append(
            {"time": timestamp, "bpm": bpm, "line": line_number, "payload_verified": verified}
        )

    groups = defaultdict(list)
    for row in valid:
        groups[row["time"]].append(row)
    readings = []
    for timestamp, group in sorted(groups.items()):
        if len({row["bpm"] for row in group}) > 1:
            for row in group:
                issue(
                    row["line"],
                    "conflicting_timestamp_rows",
                    "Multiple different BPM values share one receipt timestamp; all excluded.",
                )
            continue
        # Prefer a verified row when the same timestamp/value appears without and with a payload.
        ordered = sorted(group, key=lambda row: not row["payload_verified"])
        readings.append(ordered[0])
        for duplicate in ordered[1:]:
            issue(
                duplicate["line"],
                "duplicate_rows",
                "Repeated timestamp and BPM; included once.",
                rejected=False,
            )

    quality_keys = (
        "physical_lines",
        "blank_lines",
        "input_rows",
        "rejected_rows",
        "invalid_json_rows",
        "non_object_rows",
        "wrong_characteristic_rows",
        "invalid_timestamp_rows",
        "invalid_bpm_rows",
        "payload_present_rows",
        "payload_matching_rows",
        "payload_missing_rows",
        "malformed_payload_rows",
        "payload_mismatch_rows",
        "zero_bpm_rows",
        "out_of_order_rows",
        "duplicate_rows",
        "conflicting_timestamp_rows",
    )
    quality = {key: counts[key] for key in quality_keys}
    quality.update(
        usable_readings=len(readings),
        payload_verified_usable_readings=sum(row["payload_verified"] for row in readings),
        issue_details=details,
        issue_details_limit=40,
    )
    values = [row["bpm"] for row in readings]
    intervals = [(b["time"] - a["time"]).total_seconds() for a, b in zip(readings, readings[1:])]
    reference = median(values)
    mad = median([abs(value - reference) for value in values]) if values else None
    threshold = max(config.review_delta_bpm, config.review_mad_multiplier * mad) if values else None
    gaps = [
        {
            "start": stamp(a["time"]),
            "end": stamp(b["time"]),
            "seconds": (b["time"] - a["time"]).total_seconds(),
        }
        for a, b in zip(readings, readings[1:])
        if (b["time"] - a["time"]).total_seconds() > config.gap_seconds
    ]
    span = (readings[-1]["time"] - readings[0]["time"]).total_seconds() if readings else 0.0
    bins = defaultdict(list)
    if readings:
        for row in readings:
            bucket = int(
                (row["time"] - readings[0]["time"]).total_seconds() // config.trend_bin_seconds
            )
            bins[bucket].append(row)
    trend = [
        {
            "offset_seconds": key * config.trend_bin_seconds,
            "start": stamp(rows[0]["time"]),
            "end": stamp(rows[-1]["time"]),
            "reading_count": len(rows),
            "median_bpm": median([row["bpm"] for row in rows]),
            "min_bpm": min(row["bpm"] for row in rows),
            "max_bpm": max(row["bpm"] for row in rows),
        }
        for key, rows in sorted(bins.items())
    ]
    first_window = (
        [row["bpm"] for row in readings if (row["time"] - readings[0]["time"]).total_seconds() < 60]
        if readings
        else []
    )
    last_window = (
        [
            row["bpm"]
            for row in readings
            if (readings[-1]["time"] - row["time"]).total_seconds() < 60
        ]
        if readings
        else []
    )
    first_median, last_median = median(first_window), median(last_window)
    summary = {
        "start": stamp(readings[0]["time"]) if readings else None,
        "end": stamp(readings[-1]["time"]) if readings else None,
        "duration_seconds": span,
        "min_bpm": min(values) if values else None,
        "max_bpm": max(values) if values else None,
        "median_bpm": reference,
        "mean_bpm": statistics.mean(values) if values else None,
        "median_absolute_deviation_bpm": mad,
        "first_60s_median_bpm": first_median,
        "last_60s_median_bpm": last_median,
        "end_minus_start_window_median_bpm": (last_median - first_median)
        if values and span >= 120
        else None,
        "median_interval_seconds": median(intervals),
        "longest_interval_seconds": max(intervals) if intervals else None,
        "gaps_over_threshold": len(gaps),
        "total_gap_interval_seconds": sum(gap["seconds"] for gap in gaps),
    }
    limitations = [
        "A short recording describes this captured window only. It is not a personal baseline or a clinical assessment.",
        "Age, symptoms, medications, posture, activity, and whether the wearer was resting are unknown.",
        "Review marks are configurable within-recording statistical rules, not medical thresholds or emergency alerts.",
        "Payload agreement verifies software decoding; it does not validate the optical sensor against a clinical reference.",
        "Timestamps are Mac receipt times, not confirmed sensor acquisition times; Bluetooth delays can affect cadence.",
        "BPM alone does not supply beat-to-beat intervals. HRV, rhythm diagnoses, falls, sleep, and SpO2 are not inferred.",
    ]
    return {
        "schema_version": 1,
        "purpose": "Offline caregiver demonstration: descriptive recording review",
        "generated_at": generated_at or stamp(datetime.now(timezone.utc)),
        "source": {
            "snapshot_file": "source_snapshot.jsonl",
            "original_name": Path(source_name).name,
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
            "immutable_snapshot": True,
        },
        "config": asdict(config),
        "quality": quality,
        "summary": summary,
        "review_rule": {
            "session_median_bpm": reference,
            "effective_distance_bpm": threshold,
            "method": "max(configured BPM distance, configured multiplier × session median absolute deviation)",
            "minimum_contiguous_seconds": config.review_min_seconds,
            "gap_resets_run_seconds": config.gap_seconds,
            "clinical_threshold": False,
        },
        "review_events": review_events(readings, reference, threshold, config) if readings else [],
        "gaps": gaps,
        "trend_bins": trend,
        "readings": [
            {
                "timestamp": stamp(row["time"]),
                "bpm": row["bpm"],
                "payload_verified": row["payload_verified"],
            }
            for row in readings
        ],
        "limitations": limitations,
    }


def number(value, places=1):
    if value is None:
        return "—"
    return f"{value:,.{places}f}".rstrip("0").rstrip(".") if places else f"{value:,.0f}"


def duration(seconds):
    minutes, seconds = divmod(round(seconds), 60)
    return f"{minutes}m {seconds:02d}s" if minutes else f"{seconds}s"


def clock(value):
    return parse_timestamp(value).strftime("%H:%M:%S") if value else "—"


def chart_svg(report):
    readings, summary = report["readings"], report["summary"]
    if not readings:
        return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1100 330" role="img" aria-label="No usable measurements"><text x="550" y="165" text-anchor="middle" fill="#64736f" font-family="sans-serif" font-size="20">No usable measurements in this snapshot</text></svg>'
    start = parse_timestamp(readings[0]["timestamp"])
    span = max(summary["duration_seconds"], 1)
    low = math.floor((summary["min_bpm"] - 5) / 5) * 5
    high = math.ceil((summary["max_bpm"] + 5) / 5) * 5
    high = max(high, low + 10)
    left, top, width, height = 52, 30, 1018, 244

    def x(timestamp):
        return left + ((parse_timestamp(timestamp) - start).total_seconds() / span) * width

    def y(value):
        return top + height - (value - low) / (high - low) * height

    pieces = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1100 330" role="img" aria-labelledby="chart-title chart-desc">',
        '<title id="chart-title">Heart rate across the captured window</title>',
        f'<desc id="chart-desc">{len(readings)} usable measurements from {clock(summary["start"])} to {clock(summary["end"])} UTC; range {summary["min_bpm"]} to {summary["max_bpm"]} BPM. Lines stop at recording gaps.</desc>',
        "<style>text{font-family:Arial,sans-serif;fill:#687b75;font-size:12px}.axis{stroke:#e1e8e1;stroke-width:1}</style>",
    ]
    for event in report["review_events"]:
        pieces.append(
            f'<rect x="{x(event["start"]):.2f}" y="{top}" width="{max(1, x(event["end"]) - x(event["start"])):.2f}" height="{height}" fill="#e9b473" opacity=".22"/>'
        )
    for index in range(5):
        value = low + (high - low) * index / 4
        pos = y(value)
        pieces.append(
            f'<line class="axis" x1="{left}" y1="{pos:.2f}" x2="{left + width}" y2="{pos:.2f}"/><text x="40" y="{pos + 4:.2f}" text-anchor="end">{number(value, 0)}</text>'
        )
    for index in range(5):
        offset = span * index / 4
        pos = left + width * index / 4
        pieces.append(f'<text x="{pos:.2f}" y="301" text-anchor="middle">{duration(offset)}</text>')
    reference = summary["median_bpm"]
    pieces.append(
        f'<line x1="{left}" y1="{y(reference):.2f}" x2="{left + width}" y2="{y(reference):.2f}" stroke="#8caaa0" stroke-dasharray="5 5"/><text x="{left + width}" y="{y(reference) - 8:.2f}" text-anchor="end">Session median {number(reference)} BPM</text>'
    )
    segments, segment = [], []
    previous = None
    for row in readings:
        if (
            previous
            and (
                parse_timestamp(row["timestamp"]) - parse_timestamp(previous["timestamp"])
            ).total_seconds()
            > report["config"]["gap_seconds"]
        ):
            segments.append(segment)
            segment = []
        segment.append(f"{x(row['timestamp']):.2f},{y(row['bpm']):.2f}")
        previous = row
    segments.append(segment)
    for segment in segments:
        if len(segment) == 1:
            px, py = segment[0].split(",")
            pieces.append(f'<circle cx="{px}" cy="{py}" r="3" fill="#1c6e5d"/>')
        else:
            pieces.append(
                f'<polyline fill="none" stroke="#1c6e5d" stroke-width="2.3" stroke-linejoin="round" stroke-linecap="round" points="{" ".join(segment)}"/>'
            )
    pieces.append(
        '<text x="12" y="16">BPM</text><text x="550" y="326" text-anchor="middle">Time since first receipt · gaps are not interpolated</text></svg>'
    )
    return "".join(pieces)


def render_html(report):
    q, s, rule = report["quality"], report["summary"], report["review_rule"]
    esc = html.escape
    date_label = (
        parse_timestamp(s["start"]).strftime("%d %b %Y") if s["start"] else "No usable recording"
    )
    time_label = (
        f"{clock(s['start'])}–{clock(s['end'])} UTC"
        if s["start"]
        else "Check the input quality details below"
    )
    events = report["review_events"]
    range_label = f"{s['min_bpm']}–{s['max_bpm']}" if s["min_bpm"] is not None else "—"
    delta = s["end_minus_start_window_median_bpm"]
    delta_label = f"{delta:+g} BPM" if delta is not None else "Not enough span"
    if events:
        event_rows = "".join(
            f'<tr><td>{clock(event["start"])}<small>to {clock(event["end"])}</small></td><td>{duration(event["duration_seconds"])}</td><td><span class="tag">{esc(event["direction"])} median</span></td><td>{event["min_bpm"]}–{event["max_bpm"]} BPM</td><td>{event["reading_count"]}</td></tr>'
            for event in events
        )
        queue = f'<div class="table-wrap"><table><thead><tr><th>UTC interval</th><th>Duration</th><th>Within-session change</th><th>Observed range</th><th>Readings</th></tr></thead><tbody>{event_rows}</tbody></table></div>'
    else:
        queue = '<div class="empty"><span class="empty-icon">↗</span><div><strong>No interval met this demonstration rule.</strong><p>This describes the rule’s result for this recording. It does not establish medical normality.</p></div></div>'
    quality_rows = [
        ("Input records", q["input_rows"], "Nonblank lines in the fixed snapshot"),
        (
            "Usable unique readings",
            q["usable_readings"],
            "Validated timestamp and positive BPM; conflicts excluded",
        ),
        (
            "Payload-verified readings",
            q["payload_verified_usable_readings"],
            "Decoded BLE BPM equals the saved BPM",
        ),
        (
            "Rejected records",
            q["rejected_rows"],
            "Malformed data, disagreement, zero values, or conflicting timestamps",
        ),
        ("Repeated records removed", q["duplicate_rows"], "Same receipt timestamp and BPM"),
        ("Out-of-order arrivals", q["out_of_order_rows"], "Retained, then sorted by receipt time"),
        (
            "Recording gaps",
            s["gaps_over_threshold"],
            f"Adjacent receipts more than {number(report['config']['gap_seconds'])} seconds apart",
        ),
    ]
    quality_table = "".join(
        f'<tr><td>{esc(label)}</td><td class="num">{value:,}</td><td class="muted">{esc(explanation)}</td></tr>'
        for label, value, explanation in quality_rows
    )
    issues = "".join(
        f"<li>Line {row['line']}: {esc(row['reason'])}</li>" for row in q["issue_details"]
    )
    if not issues:
        issues = (
            "<li>No parsing, payload, duplicate, or timestamp issues found in this snapshot.</li>"
        )
    gaps = "".join(
        f"<li>{clock(gap['start'])}–{clock(gap['end'])} UTC: {number(gap['seconds'], 2)} seconds between receipts.</li>"
        for gap in report["gaps"]
    )
    limits = "".join(f"<li>{esc(value)}</li>" for value in report["limitations"])
    median_distance = number(rule["effective_distance_bpm"])
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light"><title>Care review · HR recording</title>
<style>
:root{{--ink:#183e34;--muted:#657b70;--paper:#f4f5ee;--card:#fffefa;--line:#e0e6dc;--green:#1c6e5d;--amber:#aa661b}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}main{{max-width:1260px;margin:auto;padding:32px 40px 50px}}a{{color:var(--green);text-underline-offset:3px}}.top{{display:flex;justify-content:space-between;gap:20px;align-items:center;border-bottom:1px solid var(--line);padding-bottom:24px}}.brand{{display:flex;align-items:center;gap:12px;font-size:19px;font-weight:700;letter-spacing:-.4px}}.mark{{display:grid;place-items:center;width:38px;height:38px;background:var(--ink);color:#fff;border-radius:12px}}.badge{{border:1px solid #cbd8cc;background:#eaf0e5;padding:5px 11px;border-radius:100px;font-size:11px;letter-spacing:.8px;font-weight:700;text-transform:uppercase}}.eyebrow{{font-size:11px;letter-spacing:1.8px;text-transform:uppercase;font-weight:700;color:var(--muted)}}.hero{{display:flex;justify-content:space-between;align-items:flex-end;gap:28px;padding:38px 0 28px}}h1{{font:500 42px/1.12 Georgia,serif;letter-spacing:-1.4px;margin:10px 0 12px}}.hero p{{max-width:630px;color:var(--muted);margin:0}}.date{{text-align:right;min-width:200px;font-size:14px}}.date strong{{display:block;font-size:18px}}.muted{{color:var(--muted)}}.metrics{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin-bottom:22px}}.metric,.card{{background:var(--card);border:1px solid var(--line);border-radius:16px}}.metric{{padding:21px 23px}}.metric .value{{font-size:37px;line-height:1.2;font-weight:600;letter-spacing:-1.2px;margin:8px 0}}.unit{{font-size:14px;font-weight:500;letter-spacing:0;color:var(--muted)}}.metric .caption{{font-size:12px;color:var(--muted)}}.card{{padding:26px;margin-bottom:22px}}h2{{font-size:18px;letter-spacing:-.3px;margin:0 0 6px}}.card-head{{display:flex;justify-content:space-between;align-items:flex-start;gap:15px}}.card-head p{{font-size:13px;color:var(--muted);margin:0}}.legend{{font-size:12px;white-space:nowrap}}.legend i{{display:inline-block;width:16px;height:8px;background:#e9b473;opacity:.6;margin-right:7px;border-radius:2px}}.chart{{margin-top:18px}}.chart svg{{display:block;width:100%;height:auto}}.chart-foot{{display:flex;justify-content:space-between;gap:15px;border-top:1px solid var(--line);padding-top:17px;font-size:12px;color:var(--muted)}}.chart-foot strong{{color:var(--ink);display:block;font-size:15px}}.columns{{display:grid;grid-template-columns:1.5fr 1fr;gap:22px}}.columns .card{{margin:0}}.rule{{background:#f3f2e8;border-left:3px solid #d8b27a;border-radius:5px;padding:12px 15px;font-size:12px;margin:19px 0 12px}}.rule strong{{color:#815c29}}.rule p{{margin:3px 0 0;color:var(--muted)}}table{{border-collapse:collapse;width:100%;font-size:12px}}th{{text-align:left;font-weight:600;color:var(--muted);padding:12px 8px;border-bottom:1px solid var(--line)}}td{{padding:14px 8px;border-bottom:1px solid #edf0e9;vertical-align:top}}td:first-child,th:first-child{{padding-left:0}}td small{{display:block;color:var(--muted)}}.num{{font-variant-numeric:tabular-nums;text-align:right;font-weight:600}}.tag{{display:inline-block;font-size:10px;padding:3px 7px;background:#f3e7d5;color:#89591e;border-radius:5px;white-space:nowrap}}.table-wrap{{overflow-x:auto}}.empty{{display:flex;gap:16px;padding:24px 0}}.empty-icon{{font-size:27px;background:#edf1e8;border-radius:10px;width:42px;height:42px;display:grid;place-items:center;flex:none}}.empty strong{{font-size:13px}}.empty p{{font-size:12px;margin:5px 0;color:var(--muted)}}.info-list{{list-style:none;padding:0;margin:19px 0 0}}.info-list li{{padding:11px 0;border-top:1px solid var(--line);font-size:13px}}.info-list span{{display:block;color:var(--muted);font-size:12px}}.below{{margin-top:22px}}details{{margin-top:20px;font-size:12px}}summary{{cursor:pointer;font-weight:600}}details ul{{padding-left:20px;color:var(--muted)}}.limits{{font-size:12px;color:var(--muted);padding-left:19px;margin:14px 0 0;display:grid;grid-template-columns:1fr 1fr;column-gap:35px;row-gap:9px}}footer{{padding-top:22px;display:flex;justify-content:space-between;gap:18px;font-size:11px;color:var(--muted)}}.digest{{font-family:ui-monospace,SFMono-Regular,monospace;word-break:break-all;font-size:11px}}.downloads{{white-space:nowrap}}.no-data{{background:#fff1db;color:#865b24;padding:12px 17px;border-radius:8px;margin-bottom:16px}}@media(max-width:800px){{main{{padding:22px 18px}}h1{{font-size:34px}}.hero{{align-items:flex-start;flex-direction:column;padding-top:28px}}.date{{text-align:left}}.metrics{{grid-template-columns:1fr 1fr}}.columns{{grid-template-columns:1fr}}.card{{padding:19px}}.limits{{grid-template-columns:1fr}}.card-head,.chart-foot,footer{{flex-wrap:wrap}}.top{{gap:10px}}.badge{{font-size:9px}}}}@media print{{body{{background:white}}main{{max-width:none;padding:0}}.metric,.card{{break-inside:avoid}}details{{display:block}}.downloads{{display:none}}h1{{font-size:30px}}.hero{{padding-top:20px}}}}
</style></head><body><main>
<header class="top"><div class="brand"><span class="mark" aria-hidden="true">∿</span>Care review</div><span class="badge">Local data · demonstration</span></header>
<section class="hero"><div><div class="eyebrow">A view into one recording</div><h1>See the signal. Keep the context.</h1><p>A descriptive review of the captured heart-rate stream, designed to make trends and recording quality easy to inspect.</p></div><div class="date"><strong>{date_label}</strong><span class="muted">{time_label}</span></div></section>
{'<div class="no-data">No usable measurements. Review the input-quality table before interpreting this report.</div>' if not q["usable_readings"] else ""}
<section class="metrics" aria-label="Recording summary"><article class="metric"><div class="eyebrow">Usable readings</div><div class="value">{q["usable_readings"]:,}</div><div class="caption">From {q["input_rows"]:,} input records</div></article><article class="metric"><div class="eyebrow">Session median</div><div class="value">{number(s["median_bpm"])} <span class="unit">BPM</span></div><div class="caption">Reference for this window only</div></article><article class="metric"><div class="eyebrow">Observed range</div><div class="value">{range_label}</div><div class="caption">Minimum–maximum BPM</div></article><article class="metric"><div class="eyebrow">Captured window</div><div class="value">{duration(s["duration_seconds"])}</div><div class="caption">First to last usable receipt</div></article></section>
<section class="card"><div class="card-head"><div><h2>Heart rate across the captured window</h2><p>Sensor-derived BPM plotted against receipt time.</p></div><div class="legend"><i></i>Within-session review interval</div></div><div class="chart">{chart_svg(report)}</div><div class="chart-foot"><div>Median receipt interval<strong>{number(s["median_interval_seconds"], 2)} seconds</strong></div><div>First / last 60-second median<strong>{number(s["first_60s_median_bpm"])} / {number(s["last_60s_median_bpm"])} BPM</strong></div><div>End minus start window<strong>{delta_label}</strong></div><div>Gaps &gt; {number(report["config"]["gap_seconds"])} seconds<strong>{s["gaps_over_threshold"]:,} observed</strong></div></div></section>
<div class="columns"><section class="card"><div class="card-head"><div><h2>Intervals to review</h2><p>{len(events)} interval{"s" if len(events) != 1 else ""} met the configurable demonstration rule.</p></div><span class="badge">Context needed</span></div><div class="rule"><strong>Demo rule · not a medical threshold</strong><p>At least {median_distance} BPM from the session median, in one direction, for at least {number(rule["minimum_contiguous_seconds"])} seconds. A gap over {number(rule["gap_resets_run_seconds"])} seconds resets the interval.</p></div>{queue}<p class="muted" style="font-size:12px;margin-bottom:0">A reviewer can add activity, posture, symptoms, or device-fit context. This report has none of those observations.</p></section><aside class="card"><h2>What the data supports</h2><p class="muted" style="font-size:13px;margin:0">A transparent, inspectable recording summary.</p><ul class="info-list"><li>Decoded measurement checks<span>{q["payload_verified_usable_readings"]:,} of {q["usable_readings"]:,} usable readings match their BLE payload.</span></li><li>Recorded changes over time<span>The chart uses only received measurements. Gaps are shown without filling missing data.</span></li><li>Fixed evidence for review<span>The original recorder can continue running. This report stays tied to one saved snapshot.</span></li><li>Clinical meaning needs more context<span>No diagnosis, risk score, emergency alert, or age-specific classification is produced.</span></li></ul></aside></div>
<section class="card below"><h2>Recording quality</h2><p class="muted" style="font-size:13px;margin:0 0 8px">Data checks describe this capture and decoder. They do not establish sensor accuracy.</p><div class="table-wrap"><table><thead><tr><th>Check</th><th class="num">Count</th><th>How it is handled</th></tr></thead><tbody>{quality_table}</tbody></table></div><details><summary>Inspect issue details and recording gaps</summary><ul>{issues}{gaps}</ul><p>Issue details are capped at {q["issue_details_limit"]} rows; all counts remain complete.</p></details></section>
<section class="card"><h2>Interpretation boundaries</h2><ul class="limits">{limits}</ul><details><summary>Provenance and exact analysis settings</summary><p>Built {esc(report["generated_at"])}. Snapshot: {report["source"]["bytes"]:,} bytes.</p><p class="digest">SHA-256: {report["source"]["sha256"]}</p><p>Effective review distance: max({number(report["config"]["review_delta_bpm"])} BPM, {number(report["config"]["review_mad_multiplier"])} × session MAD of {number(s["median_absolute_deviation_bpm"])} BPM). Trend summary bins: {number(report["config"]["trend_bin_seconds"])} seconds. No clinical reference range is applied.</p></details></section>
<footer><div>Computed locally from an immutable recording snapshot. All styling and graphics are embedded; this report loads no external assets.</div><div class="downloads"><a href="analysis.json" download>Analysis JSON</a> · <a href="heart_rate_trend.svg" download>Chart SVG</a></div></footer>
</main></body></html>"""


def private_write(path, data):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(data)
    temporary.replace(path)


def cli(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("heart_rate.jsonl"))
    parser.add_argument("--output-dir", type=Path, default=Path("hr_demo"))
    parser.add_argument("--gap-seconds", type=float, default=3.0)
    parser.add_argument("--review-delta-bpm", type=float, default=12.0)
    parser.add_argument("--review-mad-multiplier", type=float, default=3.0)
    parser.add_argument("--review-min-seconds", type=float, default=30.0)
    parser.add_argument("--trend-bin-seconds", type=float, default=60.0)
    args = parser.parse_args(argv)
    try:
        config = Config(
            args.gap_seconds,
            args.review_delta_bpm,
            args.review_mad_multiplier,
            args.review_min_seconds,
            args.trend_bin_seconds,
        )
        config.validate()
        # One read is the complete input for this run, even if the logger is still appending.
        data = args.input.read_bytes()
        report = analyze_bytes(data, config, args.input.name)
        args.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        snapshot = args.output_dir / "source_snapshot.jsonl"
        if args.input.resolve() != snapshot.resolve():
            if snapshot.exists() and snapshot.read_bytes() != data:
                raise ValueError(
                    "This output directory already contains a different snapshot. Choose a new --output-dir."
                )
            if not snapshot.exists():
                private_write(snapshot, data)
        private_write(
            args.output_dir / "analysis.json",
            (json.dumps(report, indent=2, allow_nan=False) + "\n").encode(),
        )
        private_write(args.output_dir / "heart_rate_trend.svg", chart_svg(report).encode())
        private_write(args.output_dir / "report.html", render_html(report).encode())
        q, s = report["quality"], report["summary"]
        print(f"Analyzed {q['usable_readings']} usable readings from {q['input_rows']} input rows.")
        print(f"Window: {s['start']} to {s['end']} ({duration(s['duration_seconds'])}).")
        print(f"Median: {number(s['median_bpm'])} BPM; range: {s['min_bpm']}–{s['max_bpm']} BPM.")
        print(
            f"Payload matches: {q['payload_verified_usable_readings']}; gaps: {s['gaps_over_threshold']}; review intervals: {len(report['review_events'])}."
        )
        print(f"Snapshot SHA-256: {report['source']['sha256']}")
        print(f"Report: {args.output_dir / 'report.html'}")
        return 0
    except (OSError, ValueError, OverflowError) as exc:
        print(f"Analysis failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(cli())
